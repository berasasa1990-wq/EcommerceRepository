import calendar
import random
import re
from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Count
from django.utils import timezone

from .models import ActiveCartItem, Category, CityVisitTotal, LiveVisitor, Product
from .visitor_geo import (
    get_client_ip,
    is_known_foreign_visitor,
    resolve_visitor_city,
    resolve_visitor_country,
)

# „Trenutno na sajtu” — aktivnost u zadnjoj minuti; stariji idu u „zadnjih 30 min”
ONLINE_MINUTES = 1
# Staff toast: kraći prozor + heartbeat/leave, da se popup skloni čim kupac ode
STAFF_TOAST_ONLINE_SECONDS = 30
# Staff popup „Kupac na sajtu” — tek ako je ostao duže od 1 minute
ONLINE_NOTIFY_AFTER_SECONDS = 60
ONLINE_NOTIFIED_CACHE_PREFIX = 'live_visitor_online_notified:'
ONLINE_NOTIFIED_CACHE_TTL = 48 * 3600
PRESENCE_CACHE_PREFIX = 'live_visitor_presence:'
LEFT_CACHE_PREFIX = 'live_visitor_left:'
PRESENCE_CACHE_TTL = STAFF_TOAST_ONLINE_SECONDS + 20
LEFT_CACHE_TTL = 300
WINDOW_MINUTES = 30
RETENTION_HOURS = 48
BOSNIA_HERZEGOVINA_COUNTRY_CODE = 'BA'
MAX_TRACKED_CATEGORIES = 8
MAX_TRACKED_PRODUCTS = 12

SOURCE_FACEBOOK = 'facebook'
SOURCE_GOOGLE = 'google'
SOURCE_INSTAGRAM = 'instagram'
SOURCE_DIRECT = 'direct'
SOURCE_OTHER = 'other'

SOURCE_LABELS = {
    SOURCE_FACEBOOK: 'Facebook',
    SOURCE_GOOGLE: 'Google',
    SOURCE_INSTAGRAM: 'Instagram',
    SOURCE_DIRECT: 'Direktno',
    SOURCE_OTHER: 'Ostalo',
}


def _display_name(user):
    if not user or not user.is_authenticated:
        return 'Gost'
    full = (user.get_full_name() or '').strip()
    if full:
        return full
    first = (user.first_name or '').strip()
    if first:
        return first
    email = (user.email or '').strip()
    if email:
        return email.split('@', 1)[0]
    return 'Registrovan korisnik'


def _display_email(user):
    if user and user.is_authenticated:
        return (user.email or '').strip()
    return ''


def _product_from_request(request):
    path = (request.path or '').rstrip('/') or '/'
    product_match = re.match(r'^/artikal/([^/]+)$', path)
    if not product_match:
        return None
    return Product.objects.filter(
        slug=product_match.group(1),
        aktivan=True,
    ).select_related('kategorija').only('pk', 'naziv', 'kategorija__naziv').first()


def _category_names_from_request(request, product=None):
    path = (request.path or '').rstrip('/') or '/'

    category_match = re.match(r'^/kategorija/([^/]+)$', path)
    if category_match:
        category = Category.objects.filter(
            slug=category_match.group(1),
            aktivan=True,
        ).only('naziv').first()
        if category:
            return [category.naziv]

    if product is None:
        product = _product_from_request(request)
    if product and product.kategorija_id and product.kategorija:
        return [product.kategorija.naziv]

    if path in ('', '/'):
        query = (request.GET.get('q') or '').strip()
        if query:
            return [f'Pretraga: {query[:48]}']

    return []


def _normalize_category_views(raw_categories):
    """
    Normalizuj istoriju kategorija.
    Podržava legacy listu stringova i novi format {naziv, views}.
    """
    history = []
    for item in (raw_categories or []):
        if isinstance(item, dict):
            name = str(item.get('naziv') or item.get('name') or '').strip()
            if not name:
                continue
            try:
                views = max(1, int(item.get('views') or 1))
            except (TypeError, ValueError):
                views = 1
            history.append({'naziv': name[:120], 'views': views})
        else:
            name = str(item or '').strip()
            if name:
                history.append({'naziv': name[:120], 'views': 1})
    return history


def _category_names_only(raw_categories):
    """Lista naziva (za staff UI / labele) — najnovije prvo."""
    return [c['naziv'] for c in _normalize_category_views(raw_categories) if c.get('naziv')]


def _merge_category_history(existing, new_items):
    """Ažuriraj listu pregledanih kategorija; povećaj views pri povratku na istu."""
    history = _normalize_category_views(existing)
    for item in new_items:
        name = (item or '').strip()[:120]
        if not name:
            continue
        matched = None
        for entry in history:
            if entry['naziv'] == name:
                matched = entry
                break
        if matched:
            matched['views'] = matched.get('views', 1) + 1
            history.remove(matched)
            history.insert(0, matched)
        else:
            history.insert(0, {'naziv': name, 'views': 1})
    return history[:MAX_TRACKED_CATEGORIES]


def _merge_product_history(existing, product):
    """Ažuriraj listu pregledanih proizvoda; povećaj views pri povratku na isti."""
    if not product:
        return list(existing or [])
    history = []
    for item in (existing or []):
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        history.append({
            'id': pid,
            'naziv': str(item.get('naziv') or '')[:120],
            'views': max(1, int(item.get('views') or 1)),
        })

    matched = None
    for entry in history:
        if entry['id'] == product.pk:
            matched = entry
            break
    if matched:
        matched['views'] = matched.get('views', 1) + 1
        matched['naziv'] = (product.naziv or matched['naziv'])[:120]
        history.remove(matched)
        history.insert(0, matched)
    else:
        history.insert(0, {
            'id': product.pk,
            'naziv': (product.naziv or '')[:120],
            'views': 1,
        })
    return history[:MAX_TRACKED_PRODUCTS]


def detect_traffic_source(request):
    """
    Izvor dolaska: Facebook, Google, Instagram, direktno, ostalo.
    UTM / click-id imaju prioritet nad HTTP Referer.
    """
    get = request.GET
    utm = (
        (get.get('utm_source') or get.get('source') or get.get('utm_medium') or '')
        .strip()
        .lower()
    )
    if get.get('fbclid') or 'facebook' in utm or utm in ('fb', 'meta', 'ig', 'fb_ad', 'facebook_ads'):
        if utm in ('ig', 'instagram') or 'instagram' in utm:
            return SOURCE_INSTAGRAM
        return SOURCE_FACEBOOK
    if get.get('gclid') or get.get('gbraid') or get.get('wbraid') or 'google' in utm or utm in ('cpc', 'adwords', 'gads'):
        return SOURCE_GOOGLE
    if get.get('igshid') or 'instagram' in utm or utm in ('ig', 'ig_ad'):
        return SOURCE_INSTAGRAM

    referer = (request.META.get('HTTP_REFERER') or '').strip().lower()
    if referer:
        if 'facebook.com' in referer or 'fb.com' in referer or 'fb.me' in referer or 'l.facebook' in referer:
            return SOURCE_FACEBOOK
        if 'instagram.com' in referer or 'l.instagram' in referer:
            return SOURCE_INSTAGRAM
        if 'google.' in referer or 'googleusercontent' in referer or 'googleapis' in referer:
            return SOURCE_GOOGLE
        # vanjski referer koji nije FB/Google/IG
        try:
            from urllib.parse import urlparse
            host = (urlparse(referer).netloc or '').lower()
            site_host = (request.get_host() or '').lower().split(':')[0]
            if host and site_host and host != site_host and not host.endswith('.' + site_host):
                return SOURCE_OTHER
        except Exception:
            pass
    return SOURCE_DIRECT


def should_track_visitor(request):
    if getattr(request, 'user', None) and request.user.is_authenticated and request.user.is_superuser:
        return False
    path = request.path or ''
    skip_prefixes = (
        '/admin/',
        '/api/',
        '/static/',
        '/media/',
        '/nalog/',
        '/priprema-pristup/',
    )
    if path == '/facebook-feed.xml':
        return False
    if any(path.startswith(prefix) for prefix in skip_prefixes):
        return False
    if request.method not in ('GET', 'POST', 'HEAD'):
        return False
    return True


def _ensure_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


def _presence_cache_key(session_key):
    return f'{PRESENCE_CACHE_PREFIX}{session_key}'


def _left_cache_key(session_key):
    return f'{LEFT_CACHE_PREFIX}{session_key}'


def touch_visitor_presence(session_key):
    """Označi sesiju aktivnom za staff toast (cache + briše left flag)."""
    if not session_key:
        return
    from django.core.cache import cache

    cache.set(_presence_cache_key(session_key), 1, PRESENCE_CACHE_TTL)
    cache.delete(_left_cache_key(session_key))


def clear_visitor_presence(session_key):
    """Odmah označi sesiju offline za staff toast."""
    if not session_key:
        return
    from django.core.cache import cache

    cache.delete(_presence_cache_key(session_key))
    cache.set(_left_cache_key(session_key), 1, LEFT_CACHE_TTL)


def is_visitor_marked_left(session_key):
    if not session_key:
        return False
    from django.core.cache import cache

    return bool(cache.get(_left_cache_key(session_key)))


def resolve_presence_session_key(request, body_session_key=''):
    """
    Session key za presence: cookie sesija ima prioritet.
    body_session_key se prihvata samo ako se poklapa sa cookie sesijom
    (ili ako cookie sesije nema — fallback za beacon edge case).
    """
    cookie_key = ''
    try:
        cookie_key = (getattr(request.session, 'session_key', None) or '').strip()
    except Exception:
        cookie_key = ''
    body_key = (body_session_key or '').strip()[:40]
    if cookie_key and body_key and cookie_key != body_key:
        # Ne dozvoli tuđu sesiju preko body-ja
        return cookie_key
    return cookie_key or body_key


def maybe_notify_visitor_online(session_key):
    """
    Staff toast „Kupac na sajtu” tek nakon ONLINE_NOTIFY_AFTER_SECONDS na sajtu.
    Jednom po sesiji (cache).
    """
    session_key = (session_key or '').strip()
    if not session_key:
        return False
    from django.core.cache import cache

    cache_key = f'{ONLINE_NOTIFIED_CACHE_PREFIX}{session_key}'
    if cache.get(cache_key):
        return False

    visitor = (
        LiveVisitor.objects.filter(session_key=session_key)
        .only('first_seen', 'last_seen', 'ime', 'email', 'grad')
        .first()
    )
    if not visitor or not visitor.first_seen:
        return False

    now = timezone.now()
    on_site_seconds = max(0, int((now - visitor.first_seen).total_seconds()))
    if on_site_seconds < ONLINE_NOTIFY_AFTER_SECONDS:
        return False

    # Mora još uvijek biti „online” (aktivnost mlađa od 1 min)
    if visitor.last_seen:
        idle_seconds = max(0, int((now - visitor.last_seen).total_seconds()))
        if idle_seconds >= ONLINE_MINUTES * 60:
            return False

    cache.set(cache_key, 1, ONLINE_NOTIFIED_CACHE_TTL)
    try:
        from .staff_alerts import notify_visitor_online
        notify_visitor_online(
            ime=visitor.ime or '',
            email=visitor.email or '',
            grad=visitor.grad or '',
            session_key=session_key,
        )
    except Exception:
        cache.delete(cache_key)
        return False
    return True


def heartbeat_live_visitor(request, body_session_key=''):
    """Laki ping dok je tab otvoren — osvježava last_seen + presence cache."""
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated and user.is_superuser:
        return False

    session_key = resolve_presence_session_key(request, body_session_key)
    if not session_key:
        session_key = _ensure_session_key(request)
    if not session_key:
        return False

    now = timezone.now()
    updated = LiveVisitor.objects.filter(session_key=session_key).update(last_seen=now)
    if not updated:
        # Nema reda (npr. prvi heartbeat) — full track ako smije
        if should_track_visitor(request):
            track_live_visitor(request)
            updated = LiveVisitor.objects.filter(session_key=session_key).exists()
    touch_visitor_presence(session_key)
    # Toast tek nakon 1 min na sajtu
    if updated or session_key:
        maybe_notify_visitor_online(session_key)
    return bool(updated) or bool(session_key)


def mark_live_visitor_left(request, body_session_key=''):
    """
    Posjetilac je zatvorio tab / otišao sa sajta.
    Cache left flag + last_seen unazad → staff toast nestaje odmah.
    """
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated and user.is_superuser:
        return False

    session_key = resolve_presence_session_key(request, body_session_key)
    if not session_key:
        return False

    clear_visitor_presence(session_key)
    offline_at = timezone.now() - timedelta(seconds=STAFF_TOAST_ONLINE_SECONDS + 60)
    LiveVisitor.objects.filter(session_key=session_key).update(last_seen=offline_at)
    return True


VISITOR_COOKIE = 'ozb_vid'
VISITOR_COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # ~13 mjeseci


def _touch_visitor_identity(request, session_key):
    """
    Trajni cookie → broj dolazaka na sajt.
    Nova sesija za isti token = +1 posjeta (nije prvi put).
    """
    import secrets

    from .models import SiteVisitorIdentity

    token = (request.COOKIES.get(VISITOR_COOKIE) or '').strip()
    if not token or len(token) < 16 or len(token) > 64:
        token = secrets.token_hex(16)
        request._ozb_vid_set = token

    identity, created = SiteVisitorIdentity.objects.get_or_create(
        token=token,
        defaults={
            'visit_count': 1,
            'last_session_key': session_key or '',
        },
    )
    if not created:
        last_sk = (identity.last_session_key or '').strip()
        if session_key and last_sk and last_sk != session_key:
            identity.visit_count = (identity.visit_count or 1) + 1
            identity.last_session_key = session_key
            identity.save(update_fields=['visit_count', 'last_session_key', 'last_seen'])
        elif session_key and not last_sk:
            identity.last_session_key = session_key
            identity.save(update_fields=['last_session_key', 'last_seen'])
        else:
            # ista sesija — samo last_seen
            SiteVisitorIdentity.objects.filter(pk=identity.pk).update(last_seen=timezone.now())
            identity.refresh_from_db(fields=['visit_count', 'last_session_key', 'last_seen'])
    return identity


def track_live_visitor(request):
    if not should_track_visitor(request):
        return
    session_key = _ensure_session_key(request)
    if not session_key:
        return

    ip = get_client_ip(request)
    if is_known_foreign_visitor(request, ip=ip):
        LiveVisitor.objects.filter(session_key=session_key).delete()
        return

    user = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
    now = timezone.now()
    country = (resolve_visitor_country(request, ip=ip) or '').strip().upper()
    if country and country != BOSNIA_HERZEGOVINA_COUNTRY_CODE:
        LiveVisitor.objects.filter(session_key=session_key).delete()
        return

    identity = None
    try:
        identity = _touch_visitor_identity(request, session_key)
    except Exception:
        identity = None

    existing_visitor = LiveVisitor.objects.filter(session_key=session_key).only(
        'pregledane_kategorije',
        'pregledani_proizvodi',
        'izvor_dolaska',
        'grad',
        'visitor_token',
        'site_visit_count',
    ).first()
    existing_grad = (existing_visitor.grad if existing_visitor else '') or ''

    grad = ''
    if not country or country == BOSNIA_HERZEGOVINA_COUNTRY_CODE:
        grad = resolve_visitor_city(request, ip=ip) or ''
    if not grad and user and user.is_authenticated:
        profil = getattr(user, 'profil', None)
        if profil and profil.grad:
            grad = profil.grad.strip()
    if not grad:
        grad = existing_grad

    product = _product_from_request(request)
    existing_categories = list(
        (existing_visitor.pregledane_kategorije if existing_visitor else None) or [],
    )
    new_categories = _category_names_from_request(request, product=product)
    if new_categories:
        existing_categories = _merge_category_history(existing_categories, new_categories)

    existing_products = list(
        (existing_visitor.pregledani_proizvodi if existing_visitor else None) or [],
    )
    if product:
        existing_products = _merge_product_history(existing_products, product)

    existing_source = (existing_visitor.izvor_dolaska if existing_visitor else '') or ''
    # Izvor se pamti pri prvom ulasku; ne prepisuj kasnije unutrašnjim navigacijama
    traffic_source = existing_source or detect_traffic_source(request)

    visit_count = 1
    visitor_token = ''
    if identity is not None:
        visit_count = max(1, int(identity.visit_count or 1))
        visitor_token = (identity.token or '')[:64]

    defaults = {
        'user': user,
        'ime': _display_name(user)[:120],
        'email': _display_email(user)[:254],
        'grad': (grad or '')[:100],
        'drzava': BOSNIA_HERZEGOVINA_COUNTRY_CODE,
        'ip_adresa': ip or None,
        'visitor_token': visitor_token,
        'site_visit_count': visit_count,
        'pregledane_kategorije': existing_categories,
        'pregledani_proizvodi': existing_products,
        'izvor_dolaska': (traffic_source or '')[:20],
        'last_seen': now,
    }
    # update pa create umjesto update_or_create (select_for_update + DEFERRED deadlock na SQLite)
    created = False
    updated = LiveVisitor.objects.filter(session_key=session_key).update(**defaults)
    if updated:
        _visitor = None
    else:
        try:
            _visitor = LiveVisitor.objects.create(session_key=session_key, **defaults)
            created = True
        except Exception:
            # Race: drugi request je upravo kreirao red
            updated = LiveVisitor.objects.filter(session_key=session_key).update(**defaults)
            if not updated:
                raise
            created = False
    touch_visitor_presence(session_key)
    # Kumulativni brojač po gradu — samo raste (nova sesija ili prvi put zabilježen grad)
    city_name = (grad or '').strip()
    if city_name and (created or not (existing_grad or '').strip()):
        record_city_visit(city_name)
    # Staff toast „Kupac na sajtu” tek nakon 1 min (heartbeat/track) — ne odmah na ulazak
    if not (user and user.is_superuser):
        maybe_notify_visitor_online(session_key)
    if random.random() < 0.02:
        cleanup_stale_live_visitors()

    # Personalizovana ponuda na osnovu gledanja (2+ pregleda artikla / top kategorija)
    try:
        from .browse_interest_offer import maybe_create_browse_interest_offer

        visitor_for_offer = LiveVisitor.objects.filter(session_key=session_key).first()
        if visitor_for_offer:
            maybe_create_browse_interest_offer(request, visitor_for_offer)
    except Exception:
        pass


def record_city_visit(grad):
    """Povećaj trajni brojač posjeta za grad (ne smanjuje se brisanjem LiveVisitor)."""
    from django.db import IntegrityError
    from django.db.models import F

    city = (grad or '').strip()[:100]
    if not city:
        return
    updated = CityVisitTotal.objects.filter(grad__iexact=city).update(
        broj_posjeta=F('broj_posjeta') + 1,
    )
    if updated:
        return
    try:
        CityVisitTotal.objects.create(grad=city, broj_posjeta=1)
    except IntegrityError:
        CityVisitTotal.objects.filter(grad__iexact=city).update(
            broj_posjeta=F('broj_posjeta') + 1,
        )


def get_city_visit_totals():
    """Ukupne posjete po gradovima, najviše → najmanje (ne zavisi od filtera datuma)."""
    rows = CityVisitTotal.objects.filter(broj_posjeta__gt=0).order_by('-broj_posjeta', 'grad')
    return [
        {
            'rank': index,
            'label': row.grad,
            'count': row.broj_posjeta,
        }
        for index, row in enumerate(rows, start=1)
    ]


def cleanup_stale_live_visitors():
    cutoff = timezone.now() - timedelta(hours=RETENTION_HOURS)
    return LiveVisitor.objects.filter(last_seen__lt=cutoff).delete()[0]


def _ago_action_label(dt, now):
    if not dt:
        return ''
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return 'upravo sada'
    if seconds < 3600:
        return f'prije {seconds // 60} min'
    if seconds < 86400:
        return f'prije {seconds // 3600} h'
    return f'prije {seconds // 86400} d'


def _build_recent_offer_map(visitors, *, now):
    """
    Mapa posljednjih staff akcija po sesiji/useru:
    - offer_map[(session|user, id)] → najnovija non-registracija ponuda (za legacy chip)
    - offers_by_key[(session|user, id)] → {tip: offer} najnoviji po tipu
    - gift_push_by_session[session]
    - gift_claim_by_session[session] / gift_claim_by_user[user]
    """
    from django.db.models import Q

    from .models import LiveVisitorOffer, OnlineGiftClaim, OnlineGiftPush

    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    empty = {
        'offer_map': {},
        'offers_by_key': {},
        'offers_list_by_session': {},
        'offers_list_by_user': {},
        'gift_push_by_session': {},
        'gift_claim_by_session': {},
        'gift_claim_by_user': {},
        'gift_pushes_by_session': {},
        'gift_claims_by_session': {},
        'gift_claims_by_user': {},
    }
    if not session_keys and not user_ids:
        return empty

    offer_cutoff = now - timedelta(minutes=WINDOW_MINUTES)
    clauses = Q()
    if session_keys:
        clauses |= Q(session_key__in=session_keys)
    if user_ids:
        clauses |= Q(user_id__in=user_ids)

    offers = (
        LiveVisitorOffer.objects.filter(clauses, azurirano__gte=offer_cutoff)
        .select_related('product')
        .order_by('-azurirano')
    )

    from collections import defaultdict

    offer_map = {}
    offers_by_key = {}
    offers_list_by_session = defaultdict(list)
    offers_list_by_user = defaultdict(list)
    for offer in offers:
        keys = []
        if offer.user_id:
            keys.append(('user', offer.user_id))
            offers_list_by_user[offer.user_id].append(offer)
        if offer.session_key:
            keys.append(('session', offer.session_key))
            offers_list_by_session[offer.session_key].append(offer)
        is_auto = False
        try:
            from .browse_interest_offer import is_auto_browse_offer

            is_auto = is_auto_browse_offer(offer)
        except Exception:
            is_auto = (getattr(offer, 'aktivacioni_kod', None) or '') == 'AUTO-BROWSE'
        for key in keys:
            bucket = offers_by_key.setdefault(key, {})
            # Auto preporuka posebno (zeleni/crveni krug) — ne gazi staff artikal-ponudu
            if is_auto:
                if 'auto_browse' not in bucket:
                    bucket['auto_browse'] = offer
            elif offer.tip not in bucket:
                bucket[offer.tip] = offer
            # Legacy chip: prefer staff, ali auto ako nema drugog
            if key not in offer_map and offer.tip != LiveVisitorOffer.Tip.REGISTRACIJA:
                offer_map[key] = offer
            elif key not in offer_map and offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
                pass
        for key in keys:
            if key not in offer_map:
                offer_map[key] = offer

    # Sve nagrade (ne samo zadnja) — potpuna istorija za staff

    gift_pushes_by_session = defaultdict(list)
    if session_keys:
        for push in (
            OnlineGiftPush.objects.filter(
                session_key__in=session_keys,
                kreirano__gte=offer_cutoff,
            )
            .select_related('campaign', 'campaign__product')
            .order_by('-kreirano')
        ):
            gift_pushes_by_session[push.session_key].append(push)

    gift_claims_by_session = defaultdict(list)
    gift_claims_by_user = defaultdict(list)
    claim_q = Q()
    if session_keys:
        claim_q |= Q(session_key__in=session_keys)
    if user_ids:
        claim_q |= Q(user_id__in=user_ids)
    if claim_q:
        for claim in (
            OnlineGiftClaim.objects.filter(claim_q, kreirano__gte=offer_cutoff)
            .select_related('campaign', 'product', 'order')
            .order_by('-kreirano')
        ):
            if claim.session_key:
                gift_claims_by_session[claim.session_key].append(claim)
            if claim.user_id:
                gift_claims_by_user[claim.user_id].append(claim)

    # Legacy: prvi (najnoviji) za stare call-site-ove
    gift_push_by_session = {
        sk: items[0] for sk, items in gift_pushes_by_session.items() if items
    }
    gift_claim_by_session = {
        sk: items[0] for sk, items in gift_claims_by_session.items() if items
    }
    gift_claim_by_user = {
        uid: items[0] for uid, items in gift_claims_by_user.items() if items
    }

    return {
        'offer_map': offer_map,
        'offers_by_key': offers_by_key,
        'offers_list_by_session': dict(offers_list_by_session),
        'offers_list_by_user': dict(offers_list_by_user),
        'gift_push_by_session': gift_push_by_session,
        'gift_claim_by_session': gift_claim_by_session,
        'gift_claim_by_user': gift_claim_by_user,
        'gift_pushes_by_session': dict(gift_pushes_by_session),
        'gift_claims_by_session': dict(gift_claims_by_session),
        'gift_claims_by_user': dict(gift_claims_by_user),
    }


def _lookup_recent_offer(actions_bundle, visitor):
    offer_map = actions_bundle.get('offer_map') or {}
    if visitor.user_id:
        offer = offer_map.get(('user', visitor.user_id))
        if offer:
            return offer
    return offer_map.get(('session', visitor.session_key))


def _lookup_offers_by_tip(actions_bundle, visitor):
    offers_by_key = actions_bundle.get('offers_by_key') or {}
    merged = {}
    if visitor.session_key:
        merged.update(offers_by_key.get(('session', visitor.session_key)) or {})
    if visitor.user_id:
        # user-level wins for same tip if newer
        for tip, offer in (offers_by_key.get(('user', visitor.user_id)) or {}).items():
            existing = merged.get(tip)
            if not existing or (offer.azurirano and existing.azurirano and offer.azurirano > existing.azurirano):
                merged[tip] = offer
    return merged


def _offer_status_fields(offer, *, visitor_online=False):
    if not offer:
        return {
            'offer_sent': False,
            'offer_active': False,
            'offer_product': '',
            'offer_product_id': None,
            'offer_status': '',
            'offer_status_label': '',
            'offer_discount_label': '',
            'offer_kind': '',
            'offer_kind_label': '',
        }

    from .models import LiveVisitorOffer

    product_id = None
    discount_label = ''
    if offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
        accepted = bool(offer.kod_aktiviran)
        active = bool(offer.show_popup) and not accepted
        product_name = 'Poziv na registraciju'
        kind = 'register'
        kind_label = 'Registracija'
        if accepted:
            status = 'accepted'
            status_label = 'Registrovao se'
        elif active and visitor_online:
            status = 'active'
            status_label = 'Poziv poslan — čeka'
        elif active:
            status = 'active'
            status_label = 'Poziv poslan'
        else:
            status = 'left'
            status_label = 'Odbio / zatvorio poziv'
    elif offer.tip == LiveVisitorOffer.Tip.NARUDZBA:
        accepted = bool(offer.kod_aktiviran)
        active = offer.show_popup and not accepted
        pct = offer.discount_percent or 0
        pct_label = int(pct) if pct == int(pct) else pct
        product_name = f'Popust {pct_label}% na narudžbu'
        discount_label = f'{pct_label}%'
        kind = 'discount'
        kind_label = 'Popust na narudžbu'
        if offer.aktivacioni_kod:
            product_name = f'{product_name} ({offer.aktivacioni_kod})'
        if offer.besplatna_dostava:
            product_name = f'{product_name} + gratis dostava'
        if accepted:
            status = 'accepted'
            status_label = 'Prihvatio / aktivirao kod'
        elif active and visitor_online:
            status = 'active'
            status_label = 'Čeka odgovor'
        else:
            status = 'left'
            status_label = 'Odbio / napustio'
    else:
        accepted = bool(offer.added_to_cart)
        active = offer.show_popup and not accepted
        product_name = ''
        product_id = offer.product_id or None
        auto_browse = False
        try:
            from .browse_interest_offer import is_auto_browse_offer

            auto_browse = is_auto_browse_offer(offer)
        except Exception:
            auto_browse = (getattr(offer, 'aktivacioni_kod', None) or '') == 'AUTO-BROWSE'
        kind = 'auto_browse' if auto_browse else 'product_offer'
        kind_label = 'Auto preporuka' if auto_browse else 'Ponuda artikla'
        if offer.product_id and offer.product:
            product_name = offer.product.naziv
        pct = offer.discount_percent or 0
        if pct:
            pct_label = int(pct) if pct == int(pct) else pct
            discount_label = f'{pct_label}%'
            if product_name:
                product_name = f'{product_name} (−{pct_label}%)'
        if offer.besplatna_dostava:
            product_name = (product_name or 'Ponuda') + ' + gratis dostava'
        if auto_browse and not product_name:
            product_name = 'Auto preporuka (gledanje)'
        if accepted:
            status = 'accepted'
            status_label = 'Prihvatio — u korpi'
        elif active and visitor_online:
            status = 'active'
            status_label = 'Čeka odgovor'
        elif active:
            status = 'active'
            status_label = 'Ponuda poslana'
        else:
            status = 'left'
            status_label = 'Odbio / zatvorio'

    return {
        'offer_sent': True,
        'offer_active': status == 'active',
        'offer_product': product_name or '',
        'offer_product_id': product_id,
        'offer_status': status,
        'offer_status_label': status_label,
        'offer_discount_label': discount_label,
        'offer_kind': kind,
        'offer_kind_label': kind_label,
    }


def _serialize_staff_action_from_offer(offer, *, visitor_online=False, now=None):
    now = now or timezone.now()
    fields = _offer_status_fields(offer, visitor_online=visitor_online)
    sent_at = offer.kreirano or offer.azurirano
    return {
        'kind': fields.get('offer_kind') or 'offer',
        'kind_label': fields.get('offer_kind_label') or 'Ponuda',
        'title': fields.get('offer_product') or fields.get('offer_kind_label') or 'Ponuda',
        'status': fields.get('offer_status') or '',
        'status_label': fields.get('offer_status_label') or '',
        'discount_label': fields.get('offer_discount_label') or '',
        'product_id': fields.get('offer_product_id'),
        'sent_at_label': _ago_action_label(sent_at, now),
        'sent_at_clock': (
            timezone.localtime(sent_at).strftime('%H:%M') if sent_at else ''
        ),
        'already_sent': True,
    }


def _gift_prize_label(*, push=None, claim=None):
    prize = ''
    if claim and claim.won:
        try:
            prize = claim.prize_label() if hasattr(claim, 'prize_label') else ''
        except Exception:
            prize = ''
    if not prize and claim and claim.product_id and claim.product:
        prize = claim.product.naziv or ''
    if not prize and push and push.campaign_id and push.campaign:
        try:
            prize = push.campaign.prize_label()
        except Exception:
            prize = push.campaign.naziv or ''
    return (prize or 'Online nagrada').strip()


def _serialize_gift_staff_action(*, push=None, claim=None, now=None):
    """
    Online nagrada — statusi:
    osvojio (accepted), izgubio (lost), odbio (dismissed), čeka (pending).
    """
    now = now or timezone.now()
    prize = _gift_prize_label(push=push, claim=claim)

    if claim:
        sent_at = claim.kreirano
        source = 'auto' if not push else 'manual'
        if claim.won:
            if claim.reward_consumed or claim.order_id:
                status, status_label, gift_result = 'accepted', 'Osvojio', 'osvojio'
                if claim.order_id and getattr(claim, 'order', None):
                    status_label = f'Osvojio · #{claim.order.broj}'
                elif claim.reward_consumed:
                    status_label = 'Osvojio · iskoristio'
            else:
                status, status_label, gift_result = 'accepted', 'Osvojio', 'osvojio'
        else:
            status, status_label, gift_result = 'lost', 'Izgubio', 'izgubio'
        event_id = f'claim-{claim.pk}'
        product_id = claim.product_id
    elif push:
        sent_at = push.kreirano
        source = 'manual'
        product_id = None
        event_id = f'push-{push.pk}'
        if push.dismissed and not push.played:
            status, status_label, gift_result = 'dismissed', 'Odbio', 'odbio'
        elif push.dismissed and push.played:
            # Otvorio pa zatvorio bez claima — tretira se kao odbio
            status, status_label, gift_result = 'dismissed', 'Odbio', 'odbio'
        elif push.played:
            status, status_label, gift_result = 'pending', 'Otvorio — čeka', 'ceka'
        else:
            status, status_label, gift_result = 'pending', 'Čeka otvaranje', 'ceka'
    else:
        return None

    return {
        'kind': 'gift',
        'kind_label': 'Online nagrada',
        'title': prize,
        'status': status,
        'status_label': status_label,
        'gift_result': gift_result,  # osvojio | izgubio | odbio | ceka
        'discount_label': '',
        'product_id': product_id,
        'event_id': event_id,
        'sent_at_label': _ago_action_label(sent_at, now),
        'sent_at_clock': (
            timezone.localtime(sent_at).strftime('%H:%M') if sent_at else ''
        ),
        'sent_at_ts': sent_at.timestamp() if sent_at else 0,
        'already_sent': True,
        'source': source,
    }


def _staff_gift_actions_for_visitor(visitor, actions_bundle, *, now=None):
    """
    Svi događaji nagrade (ne briši / ne spajaj u jedan).
    Claims + push-evi koji još nemaju claim u istom vremenskom prozoru.
    """
    now = now or timezone.now()
    actions = []
    claims = []
    if visitor.session_key:
        claims.extend(
            (actions_bundle.get('gift_claims_by_session') or {}).get(visitor.session_key)
            or []
        )
    if visitor.user_id:
        for c in (actions_bundle.get('gift_claims_by_user') or {}).get(visitor.user_id) or []:
            if c not in claims:
                claims.append(c)

    # Dedup claims po pk
    seen_claim = set()
    unique_claims = []
    for c in claims:
        if c.pk in seen_claim:
            continue
        seen_claim.add(c.pk)
        unique_claims.append(c)

    claim_session_keys = {c.session_key for c in unique_claims if c.session_key}
    claim_times = [c.kreirano for c in unique_claims if c.kreirano]

    for claim in unique_claims:
        # Poveži push blizu claima (isti session) — opcionalno za source
        push = None
        if claim.session_key:
            for p in (actions_bundle.get('gift_pushes_by_session') or {}).get(claim.session_key) or []:
                # isti campaign, push prije ili blizu claima
                if claim.campaign_id and p.campaign_id == claim.campaign_id:
                    push = p
                    break
        action = _serialize_gift_staff_action(push=push, claim=claim, now=now)
        if action:
            actions.append(action)

    # Push-evi bez claima (čekaju / odbio) — svi ostaju u listi
    pushes = []
    if visitor.session_key:
        pushes = list(
            (actions_bundle.get('gift_pushes_by_session') or {}).get(visitor.session_key)
            or []
        )
    for push in pushes:
        # Ako postoji claim za isti campaign + session, push je već pokriven claimom
        has_claim = any(
            c.campaign_id == push.campaign_id and c.session_key == push.session_key
            for c in unique_claims
        )
        if has_claim:
            continue
        action = _serialize_gift_staff_action(push=push, claim=None, now=now)
        if action:
            actions.append(action)

    return actions


def _staff_actions_for_visitor(visitor, actions_bundle, *, visitor_online=False, now=None):
    """Sve poslane staff akcije + ishod — potpuna istorija (odbio + prihvatio ostaju)."""
    now = now or timezone.now()
    from .models import LiveVisitorOffer

    actions = []
    seen_offer_ids = set()

    # SVE ponude (ne samo zadnja po tipu) — da stoje i odbijene i prihvaćene
    offer_lists = []
    if visitor.session_key:
        offer_lists.append(
            (actions_bundle.get('offers_list_by_session') or {}).get(visitor.session_key) or []
        )
    if visitor.user_id:
        offer_lists.append(
            (actions_bundle.get('offers_list_by_user') or {}).get(visitor.user_id) or []
        )
    for offer_list in offer_lists:
        for offer in offer_list:
            if offer.pk in seen_offer_ids:
                continue
            seen_offer_ids.add(offer.pk)
            action = _serialize_staff_action_from_offer(
                offer, visitor_online=visitor_online, now=now,
            )
            if (
                offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA
                and visitor.user_id
                and action.get('status') != 'accepted'
            ):
                action['status'] = 'accepted'
                action['status_label'] = 'Registrovao se'
            try:
                action['sent_at_ts'] = (
                    offer.azurirano or offer.kreirano
                ).timestamp()
            except Exception:
                action['sent_at_ts'] = 0
            action['event_id'] = f'offer-{offer.pk}'
            actions.append(action)

    # Fallback: stari bucket po tipu (ako lista prazna)
    if not actions:
        by_tip = _lookup_offers_by_tip(actions_bundle, visitor)
        for tip in (
            'auto_browse',
            LiveVisitorOffer.Tip.ARTIKAL,
            LiveVisitorOffer.Tip.NARUDZBA,
            LiveVisitorOffer.Tip.REGISTRACIJA,
        ):
            offer = by_tip.get(tip)
            if not offer or offer.pk in seen_offer_ids:
                continue
            seen_offer_ids.add(offer.pk)
            action = _serialize_staff_action_from_offer(
                offer, visitor_online=visitor_online, now=now,
            )
            try:
                action['sent_at_ts'] = (offer.azurirano or offer.kreirano).timestamp()
            except Exception:
                action['sent_at_ts'] = 0
            action['event_id'] = f'offer-{offer.pk}'
            actions.append(action)

    # Sve nagrade (osvojio / izgubio / odbio / čeka)
    actions.extend(_staff_gift_actions_for_visitor(visitor, actions_bundle, now=now))

    # Najnovije prvo
    actions.sort(key=lambda a: float(a.get('sent_at_ts') or 0), reverse=True)
    return actions


def _build_cart_presence_map(visitors):
    """
    Vraća:
    sessions_with_cart, users_with_cart,
    cart_value_by_session, cart_value_by_user,
    cart_items_by_session, cart_items_by_user
    """
    from collections import defaultdict
    from decimal import Decimal

    from django.db.models import Sum

    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    sessions_with_cart = set()
    users_with_cart = set()
    cart_value_by_session = {}
    cart_value_by_user = {}
    cart_items_by_session = defaultdict(list)
    cart_items_by_user = defaultdict(list)

    if session_keys:
        session_rows = (
            ActiveCartItem.objects.filter(session_key__in=session_keys)
            .values('session_key')
            .annotate(total=Sum('ukupno'))
        )
        for row in session_rows:
            key = row['session_key']
            total = row['total'] or Decimal('0')
            sessions_with_cart.add(key)
            cart_value_by_session[key] = total

        for item in (
            ActiveCartItem.objects.filter(session_key__in=session_keys)
            .select_related('product', 'variation')
            .order_by('-azurirano', '-id')
        ):
            cart_items_by_session[item.session_key].append(_serialize_cart_line(item))

    if user_ids:
        user_rows = (
            ActiveCartItem.objects.filter(user_id__in=user_ids)
            .values('user_id')
            .annotate(total=Sum('ukupno'))
        )
        for row in user_rows:
            uid = row['user_id']
            total = row['total'] or Decimal('0')
            users_with_cart.add(uid)
            cart_value_by_user[uid] = total

        for item in (
            ActiveCartItem.objects.filter(user_id__in=user_ids)
            .select_related('product', 'variation')
            .order_by('-azurirano', '-id')
        ):
            cart_items_by_user[item.user_id].append(_serialize_cart_line(item))

    return (
        sessions_with_cart,
        users_with_cart,
        cart_value_by_session,
        cart_value_by_user,
        dict(cart_items_by_session),
        dict(cart_items_by_user),
    )


def _serialize_cart_line(item):
    """JSON-friendly stavka aktivne korpe za staff live UI."""
    image_url = ''
    product_url = ''
    try:
        if item.variation_id and item.variation and item.variation.slika:
            image_url = item.variation.slika.url
        elif item.product_id and item.product and item.product.prikazna_slika:
            image_url = item.product.prikazna_slika.url
    except Exception:
        image_url = ''
    try:
        if item.product_id and item.product:
            product_url = item.product.get_absolute_url()
    except Exception:
        product_url = ''
    name = (item.naziv or '').strip() or 'Artikal'
    var_name = (item.varijacija_naziv or '').strip()
    try:
        cijena = item.cijena
        ukupno = item.ukupno
    except Exception:
        cijena = 0
        ukupno = 0
    return {
        'product_id': item.product_id,
        'naziv': name,
        'varijacija': var_name,
        'kolicina': int(item.kolicina or 1),
        'cijena': str(cijena),
        'cijena_label': f'{cijena} KM',
        'ukupno': str(ukupno),
        'ukupno_label': f'{ukupno} KM',
        'image_url': image_url,
        'product_url': product_url,
    }


def _visitor_has_cart(visitor, sessions_with_cart, users_with_cart):
    if visitor.user_id and visitor.user_id in users_with_cart:
        return True
    return visitor.session_key in sessions_with_cart


def _visitor_cart_value(visitor, cart_value_by_session, cart_value_by_user):
    from decimal import Decimal

    if visitor.user_id and visitor.user_id in cart_value_by_user:
        return cart_value_by_user[visitor.user_id]
    return cart_value_by_session.get(visitor.session_key) or Decimal('0')


def _visitor_cart_items(visitor, cart_items_by_session, cart_items_by_user):
    """Preferiraj korpu po sesiji; ako prazna, po useru."""
    items = []
    if visitor.session_key:
        items = list(cart_items_by_session.get(visitor.session_key) or [])
    if not items and visitor.user_id:
        items = list(cart_items_by_user.get(visitor.user_id) or [])
    return items


def _parse_date_param(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _parse_month_param(value):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(f'{value}-01', '%Y-%m-%d').date()
    except ValueError:
        return None


def _month_end(value):
    last_day = calendar.monthrange(value.year, value.month)[1]
    return value.replace(day=last_day)


def _aware_day_start(value):
    """Start of a local calendar day as an aware datetime (inclusive bound)."""
    return timezone.make_aware(
        datetime.combine(value, time.min),
        timezone.get_current_timezone(),
    )


def _aware_day_end_exclusive(value):
    """Start of the next local calendar day (exclusive upper bound)."""
    return _aware_day_start(value + timedelta(days=1))


def get_traffic_filter_defaults():
    today = timezone.localdate()
    year = today.year
    month = today.month - 11
    while month <= 0:
        month += 12
        year -= 1
    return {
        'daily_from': today.isoformat(),
        'daily_to': today.isoformat(),
        'monthly_from': f'{year:04d}-{month:02d}',
        'monthly_to': today.strftime('%Y-%m'),
    }


def parse_traffic_filters(request):
    defaults = get_traffic_filter_defaults()
    daily_from = _parse_date_param(request.GET.get('daily_from')) if request else None
    daily_to = _parse_date_param(request.GET.get('daily_to')) if request else None
    monthly_from = _parse_month_param(request.GET.get('monthly_from')) if request else None
    monthly_to = _parse_month_param(request.GET.get('monthly_to')) if request else None

    if daily_from is None:
        daily_from = _parse_date_param(defaults['daily_from'])
    if daily_to is None:
        daily_to = _parse_date_param(defaults['daily_to'])
    if daily_from and daily_to and daily_from > daily_to:
        daily_from, daily_to = daily_to, daily_from

    if monthly_from is None:
        monthly_from = _parse_month_param(defaults['monthly_from'])
    if monthly_to is None:
        monthly_to = _parse_month_param(defaults['monthly_to'])
    if monthly_from and monthly_to and monthly_from > monthly_to:
        monthly_from, monthly_to = monthly_to, monthly_from

    return {
        'daily_from': daily_from.isoformat() if daily_from else defaults['daily_from'],
        'daily_to': daily_to.isoformat() if daily_to else defaults['daily_to'],
        'monthly_from': (
            monthly_from.strftime('%Y-%m') if monthly_from else defaults['monthly_from']
        ),
        'monthly_to': (
            monthly_to.strftime('%Y-%m') if monthly_to else defaults['monthly_to']
        ),
        'daily_from_date': daily_from,
        'daily_to_date': daily_to,
        'monthly_from_date': monthly_from,
        'monthly_to_date': monthly_to,
    }


def get_visitor_traffic_stats(
    *,
    daily_from=None,
    daily_to=None,
    monthly_from=None,
    monthly_to=None,
):
    """
    Group LiveVisitor.first_seen by local day/month.

    Avoids TruncDate/TruncMonth and __date lookups on SQLite: Django's
    django_datetime_cast_date UDF raises when a row stores a date-only string
    (e.g. '2026-07-11') instead of a full timestamp.
    """
    daily_qs = LiveVisitor.objects.all()
    if daily_from:
        daily_qs = daily_qs.filter(first_seen__gte=_aware_day_start(daily_from))
    if daily_to:
        daily_qs = daily_qs.filter(first_seen__lt=_aware_day_end_exclusive(daily_to))

    daily_counts = Counter()
    for first_seen in daily_qs.values_list('first_seen', flat=True).iterator():
        if not first_seen:
            continue
        day = timezone.localtime(first_seen).date()
        daily_counts[day] += 1

    daily_stats = [
        {'label': day.strftime('%d.%m.%Y.'), 'count': count}
        for day, count in sorted(daily_counts.items(), reverse=True)
    ]

    monthly_qs = LiveVisitor.objects.all()
    if monthly_from:
        monthly_qs = monthly_qs.filter(first_seen__gte=_aware_day_start(monthly_from))
    if monthly_to:
        monthly_qs = monthly_qs.filter(
            first_seen__lt=_aware_day_end_exclusive(_month_end(monthly_to)),
        )

    monthly_counts = Counter()
    for first_seen in monthly_qs.values_list('first_seen', flat=True).iterator():
        if not first_seen:
            continue
        local = timezone.localtime(first_seen)
        monthly_counts[(local.year, local.month)] += 1

    monthly_stats = [
        {'label': f'{month:02d}/{year}', 'count': count}
        for (year, month), count in sorted(monthly_counts.items(), reverse=True)
    ]

    return {
        'daily': daily_stats,
        'monthly': monthly_stats,
        # Kumulativno — ne zavisi od filtera datuma, samo raste
        'by_city': get_city_visit_totals(),
    }


def _format_time_on_site(seconds):
    """Čitljivo vrijeme provedeno na sajtu (npr. 45 s, 12 min, 1 h 5 min)."""
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f'{seconds} s'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} min'
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        if rem_min:
            return f'{hours} h {rem_min} min'
        return f'{hours} h'
    days = hours // 24
    rem_h = hours % 24
    if rem_h:
        return f'{days} d {rem_h} h'
    return f'{days} d'


def _build_site_buyer_stats(visitors):
    """
    Korisnici / emailovi koji su već poručili preko sajta.
    Vraća (buyer_user_ids, buyer_emails, purchase_count_by_user, purchase_count_by_email).
    """
    from django.db.models import Count
    from django.db.models.functions import Lower

    from .models import Order

    user_ids = [v.user_id for v in visitors if v.user_id]
    emails = []
    for visitor in visitors:
        email = (visitor.email or '').strip().lower()
        if email:
            emails.append(email)
        user = getattr(visitor, 'user', None)
        if user is not None:
            user_email = (getattr(user, 'email', None) or '').strip().lower()
            if user_email:
                emails.append(user_email)
    emails = list(set(emails))

    buyer_user_ids = set()
    buyer_emails = set()
    purchase_count_by_user = {}
    purchase_count_by_email = {}
    orders = Order.objects.exclude(status=Order.Status.OTKAZANA)
    if user_ids:
        for row in (
            orders.filter(korisnik_id__in=user_ids)
            .values('korisnik_id')
            .annotate(cnt=Count('pk'))
        ):
            uid = row['korisnik_id']
            cnt = row['cnt'] or 0
            if cnt:
                buyer_user_ids.add(uid)
                purchase_count_by_user[uid] = cnt
    if emails:
        for row in (
            orders.annotate(email_l=Lower('email'))
            .filter(email_l__in=emails)
            .values('email_l')
            .annotate(cnt=Count('pk'))
        ):
            em = row['email_l']
            cnt = row['cnt'] or 0
            if cnt and em:
                buyer_emails.add(em)
                purchase_count_by_email[em] = cnt
    return buyer_user_ids, buyer_emails, purchase_count_by_user, purchase_count_by_email


def _visitor_purchase_info(visitor, buyer_user_ids, buyer_emails, purchase_count_by_user, purchase_count_by_email):
    count = 0
    if visitor.user_id and visitor.user_id in purchase_count_by_user:
        count = purchase_count_by_user[visitor.user_id]
    email = (visitor.email or '').strip().lower()
    if not count and email and email in purchase_count_by_email:
        count = purchase_count_by_email[email]
    user = getattr(visitor, 'user', None)
    if not count and user is not None:
        user_email = (getattr(user, 'email', None) or '').strip().lower()
        if user_email and user_email in purchase_count_by_email:
            count = purchase_count_by_email[user_email]
    has_purchased = bool(count) or (
        (visitor.user_id and visitor.user_id in buyer_user_ids)
        or (email and email in buyer_emails)
    )
    return has_purchased, count


def _build_site_buyer_sets(visitors):
    """Kompatibilnost sa staff_alerts — (buyer_user_ids, buyer_emails)."""
    buyer_user_ids, buyer_emails, _, _ = _build_site_buyer_stats(visitors)
    return buyer_user_ids, buyer_emails


def _visitor_has_purchased(visitor, buyer_user_ids, buyer_emails):
    """Kompatibilnost sa staff_alerts."""
    has_purchased, _count = _visitor_purchase_info(
        visitor, buyer_user_ids, buyer_emails, {}, {},
    )
    # Ako nema count mapa, fallback na setove
    if has_purchased:
        return True
    if visitor.user_id and visitor.user_id in buyer_user_ids:
        return True
    email = (visitor.email or '').strip().lower()
    if email and email in buyer_emails:
        return True
    user = getattr(visitor, 'user', None)
    if user is not None:
        user_email = (getattr(user, 'email', None) or '').strip().lower()
        if user_email and user_email in buyer_emails:
            return True
    return False


def _normalize_product_views(raw_products):
    products = []
    for item in (raw_products or []):
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        try:
            views = max(1, int(item.get('views') or 1))
        except (TypeError, ValueError):
            views = 1
        naziv = str(item.get('naziv') or '').strip()[:120]
        products.append({'id': pid, 'naziv': naziv, 'views': views})
    return products


def _visitor_payload(
    visitor,
    *,
    now,
    offer=None,
    staff_actions=None,
    has_cart=False,
    cart_value=None,
    cart_items=None,
    has_purchased=False,
    purchase_count=0,
):
    from decimal import Decimal

    seconds_ago = max(0, int((now - visitor.last_seen).total_seconds()))
    if seconds_ago < 60:
        ago_label = 'upravo sada'
    elif seconds_ago < 3600:
        minutes = seconds_ago // 60
        ago_label = f'prije {minutes} min'
    else:
        hours = seconds_ago // 3600
        ago_label = f'prije {hours} h'

    # Online samo dok je aktivnost mlađa od 1 min; „prije 1 min” i starije → prozor 30 min
    is_online = seconds_ago < ONLINE_MINUTES * 60
    # Online: od ulaska do sada; offline: od ulaska do zadnje aktivnosti
    end_time = now if is_online else visitor.last_seen
    first_seen = visitor.first_seen or visitor.last_seen or now
    time_on_site_seconds = max(0, int((end_time - first_seen).total_seconds()))
    time_on_site_label = _format_time_on_site(time_on_site_seconds)

    grad = ''
    if (visitor.drzava or '').strip().upper() == BOSNIA_HERZEGOVINA_COUNTRY_CODE:
        grad = (visitor.grad or '').strip()
        if not grad and visitor.user_id and getattr(visitor, 'user', None):
            profil = getattr(visitor.user, 'profil', None)
            if profil and profil.grad:
                grad = profil.grad.strip()

    category_views = _normalize_category_views(visitor.pregledane_kategorije)
    categories = [c['naziv'] for c in category_views if c.get('naziv')]
    products = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    products_viewed_count = len(products)
    almost_cart = []
    try:
        from .almost_cart import get_almost_cart_products

        almost_cart = get_almost_cart_products(visitor)
    except Exception:
        almost_cart = []
    revisited = [p for p in products if p.get('views', 1) > 1]
    returned_to_product = bool(revisited)
    returned_products = [
        {
            'id': p['id'],
            'naziv': p.get('naziv') or '',
            'views': p.get('views') or 1,
        }
        for p in revisited
    ]
    returned_products_label = ', '.join(
        f"{p['naziv']} ({p['views']}×)" for p in returned_products[:3] if p.get('naziv')
    )
    if len(returned_products) > 3:
        returned_products_label = f'{returned_products_label}…'

    source_key = (getattr(visitor, 'izvor_dolaska', None) or SOURCE_DIRECT).strip().lower()
    if source_key not in SOURCE_LABELS:
        source_key = SOURCE_OTHER if source_key else SOURCE_DIRECT
    source_label = SOURCE_LABELS.get(source_key, SOURCE_LABELS[SOURCE_DIRECT])

    if cart_value is None:
        cart_value = Decimal('0')
    try:
        cart_value_dec = Decimal(str(cart_value or 0)).quantize(Decimal('0.01'))
    except Exception:
        cart_value_dec = Decimal('0.00')
    cart_value_label = f'{cart_value_dec:.2f} KM' if cart_value_dec > 0 else '—'

    is_registered = bool(visitor.user_id)
    products_label = ', '.join(p['naziv'] for p in products[:4] if p.get('naziv'))
    if products_viewed_count > 4:
        products_label = f'{products_label}…'

    purchase_label = ''
    if has_purchased:
        if purchase_count > 1:
            purchase_label = f'{purchase_count} kupovine'
        elif purchase_count == 1:
            purchase_label = '1 kupovina'
        else:
            purchase_label = 'Kupovao ranije'

    # Pametne preporuke za staff (šta ručno poslati) + psihologija gledanja
    sell_recs = []
    visitor_insight = {}
    offer_outcomes = {'accepted': [], 'rejected': [], 'pending': []}
    try:
        from .browse_interest_offer import (
            build_sell_recommendations,
            build_visitor_insight,
            get_offer_outcome_summary,
        )

        visitor_insight = build_visitor_insight(visitor) or {}
        sell_recs = build_sell_recommendations(visitor, limit=4) or []
        offer_outcomes = get_offer_outcome_summary(visitor) or offer_outcomes
    except Exception:
        sell_recs = []
        visitor_insight = {}

    top_sell = sell_recs[0] if sell_recs else None

    payload = {
        'session_key': visitor.session_key,
        'user_id': visitor.user_id or None,
        'ime': visitor.ime or 'Gost',
        'email': visitor.email or '',
        'grad': grad,
        'categories': categories,
        'categories_label': ', '.join(
            f"{c['naziv']} ({c['views']}×)" if c.get('views', 1) > 1 else c['naziv']
            for c in category_views[:6]
            if c.get('naziv')
        ),
        'products': products,
        'products_viewed_count': products_viewed_count,
        'products_label': products_label,
        'returned_to_product': returned_to_product,
        'returned_products': returned_products,
        'returned_products_label': returned_products_label,
        'traffic_source': source_key,
        'traffic_source_label': source_label,
        'cart_value': str(cart_value_dec),
        'cart_value_label': cart_value_label,
        'has_cart': has_cart or cart_value_dec > 0,
        'cart_items': list(cart_items or []),
        'cart_items_count': len(list(cart_items or [])),
        'has_purchased': bool(has_purchased),
        'purchase_count': int(purchase_count or 0),
        'purchase_label': purchase_label,
        'is_registered': is_registered,
        'is_guest': not is_registered and not visitor.email,
        'can_invite_register': not is_registered,
        'can_email_offer': bool((visitor.email or '').strip()),
        'last_seen': visitor.last_seen,
        'last_seen_label': ago_label,
        'seconds_ago': seconds_ago,
        'time_on_site_seconds': time_on_site_seconds,
        'time_on_site_label': time_on_site_label,
        'is_online': is_online,
        'site_visit_count': int(getattr(visitor, 'site_visit_count', 1) or 1),
        'is_returning_visitor': int(getattr(visitor, 'site_visit_count', 1) or 1) > 1,
        'returning_label': (
            f'Vraćeni posjetilac ({int(getattr(visitor, "site_visit_count", 1) or 1)}× na sajtu)'
            if int(getattr(visitor, 'site_visit_count', 1) or 1) > 1
            else 'Prvi put na sajtu'
        ),
        'visitor_insight': visitor_insight,
        'sell_recommendations': sell_recs,
        'top_sell_recommendation': top_sell,
        'top_sell_label': (
            f"{top_sell.get('naziv')} — {top_sell.get('reason')}"
            if top_sell else ''
        ),
        'almost_cart': almost_cart,
        'almost_cart_count': len(almost_cart),
        'almost_cart_label': (
            f"{almost_cart[0].get('naziv')} ({almost_cart[0].get('hovers')}× hover)"
            if almost_cart else ''
        ),
        'offer_outcomes': offer_outcomes,
        'accepted_offer_ids': [
            r.get('product_id') for r in (offer_outcomes.get('accepted') or [])
            if r.get('product_id')
        ],
        'rejected_offer_ids': [
            r.get('product_id') for r in (offer_outcomes.get('rejected') or [])
            if r.get('product_id')
        ],
    }
    payload.update(_offer_status_fields(offer, visitor_online=payload['is_online']))
    actions = list(staff_actions or [])
    payload['staff_actions'] = actions
    payload['has_staff_actions'] = bool(actions)
    # Flagovi da se ne šalje isto 2× (UI disabled / badge)
    payload['sent_product_offer'] = any(a.get('kind') == 'product_offer' for a in actions)
    payload['sent_discount_offer'] = any(a.get('kind') == 'discount' for a in actions)
    payload['sent_register_invite'] = any(a.get('kind') == 'register' for a in actions)
    payload['sent_gift'] = any(a.get('kind') == 'gift' for a in actions)
    # Šta je kupac uradio (sažetak za karticu)
    if actions:
        latest = actions[0]
        # prefer active then accepted then left for card chip
        for preferred in ('active', 'accepted', 'left'):
            match = next((a for a in actions if a.get('status') == preferred), None)
            if match:
                latest = match
                break
        payload['staff_action_summary'] = (
            f"{latest.get('kind_label')}: {latest.get('status_label')}"
        )
        payload['staff_action_summary_status'] = latest.get('status') or ''
    else:
        payload['staff_action_summary'] = ''
        payload['staff_action_summary_status'] = ''
    return payload


def get_live_visitor_snapshot():
    now = timezone.now()
    window_cutoff = now - timedelta(minutes=WINDOW_MINUTES)

    window_qs = LiveVisitor.objects.filter(
        last_seen__gte=window_cutoff,
        drzava=BOSNIA_HERZEGOVINA_COUNTRY_CODE,
    ).select_related('user__profil').order_by('-last_seen')
    visitor_rows = list(window_qs)
    actions_bundle = _build_recent_offer_map(visitor_rows, now=now)
    (
        sessions_with_cart,
        users_with_cart,
        cart_value_by_session,
        cart_value_by_user,
        cart_items_by_session,
        cart_items_by_user,
    ) = _build_cart_presence_map(visitor_rows)
    (
        buyer_user_ids,
        buyer_emails,
        purchase_count_by_user,
        purchase_count_by_email,
    ) = _build_site_buyer_stats(visitor_rows)

    window_visitors = []
    for row in visitor_rows:
        has_purchased, purchase_count = _visitor_purchase_info(
            row,
            buyer_user_ids,
            buyer_emails,
            purchase_count_by_user,
            purchase_count_by_email,
        )
        # is_online se računa unutar payload — privremeno za actions
        seconds_ago = max(0, int((now - row.last_seen).total_seconds())) if row.last_seen else 9999
        is_online_tmp = seconds_ago < ONLINE_MINUTES * 60
        staff_actions = _staff_actions_for_visitor(
            row, actions_bundle, visitor_online=is_online_tmp, now=now,
        )
        window_visitors.append(
            _visitor_payload(
                row,
                now=now,
                offer=_lookup_recent_offer(actions_bundle, row),
                staff_actions=staff_actions,
                has_cart=_visitor_has_cart(row, sessions_with_cart, users_with_cart),
                cart_value=_visitor_cart_value(row, cart_value_by_session, cart_value_by_user),
                cart_items=_visitor_cart_items(row, cart_items_by_session, cart_items_by_user),
                has_purchased=has_purchased,
                purchase_count=purchase_count,
            )
        )

    online_visitors = [row for row in window_visitors if row['is_online']]
    registered_online = [row for row in online_visitors if row.get('is_registered')]
    registered_window = [row for row in window_visitors if row.get('is_registered')]

    return {
        'online_count': len(online_visitors),
        'window_count': len(window_visitors),
        'registered_online_count': len(registered_online),
        'registered_window_count': len(registered_window),
        'online_visitors': online_visitors,
        'window_visitors': window_visitors,
        'registered_online_visitors': registered_online,
        'registered_window_visitors': registered_window,
        'online_minutes': ONLINE_MINUTES,
        'window_minutes': WINDOW_MINUTES,
        'generated_at': now,
    }


def get_registered_customers(*, online_user_ids=None):
    """
    Svi registrovani kupci (ne-staff) s emailom, uključujući neaktivirane naloge.
    online_user_ids — set user_id koji su trenutno online (za badge).
    """
    from django.contrib.auth.models import User

    online_user_ids = set(online_user_ids or [])
    users = (
        User.objects.filter(is_superuser=False, is_staff=False)
        .exclude(email='')
        .select_related('profil')
        # Neaktivirani prvo, zatim po emailu
        .order_by('is_active', 'email')
    )
    rows = []
    for user in users:
        email = (user.email or '').strip()
        if not email or '@' not in email:
            continue
        full = (user.get_full_name() or '').strip()
        if not full:
            full = (user.first_name or '').strip() or email.split('@', 1)[0]
        grad = ''
        profil = getattr(user, 'profil', None)
        if profil and profil.grad:
            grad = profil.grad.strip()
        rows.append({
            'user_id': user.pk,
            'ime': full[:120],
            'email': email,
            'grad': grad,
            'is_online': user.pk in online_user_ids,
            'is_active': bool(user.is_active),
            'account_status_label': 'Aktivan' if user.is_active else 'Nije aktiviran',
        })
    return rows