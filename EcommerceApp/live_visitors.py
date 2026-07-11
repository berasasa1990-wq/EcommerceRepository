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


def _merge_category_history(existing, new_items):
    history = list(existing or [])
    for item in new_items:
        name = (item or '').strip()
        if not name:
            continue
        if name in history:
            history.remove(name)
        history.insert(0, name)
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

    existing_visitor = LiveVisitor.objects.filter(session_key=session_key).only(
        'pregledane_kategorije',
        'pregledani_proizvodi',
        'izvor_dolaska',
        'grad',
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

    defaults = {
        'user': user,
        'ime': _display_name(user)[:120],
        'email': _display_email(user)[:254],
        'grad': (grad or '')[:100],
        'drzava': BOSNIA_HERZEGOVINA_COUNTRY_CODE,
        'ip_adresa': ip or None,
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
        'gift_push_by_session': {},
        'gift_claim_by_session': {},
        'gift_claim_by_user': {},
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

    offer_map = {}
    offers_by_key = {}
    for offer in offers:
        keys = []
        if offer.user_id:
            keys.append(('user', offer.user_id))
        if offer.session_key:
            keys.append(('session', offer.session_key))
        for key in keys:
            bucket = offers_by_key.setdefault(key, {})
            if offer.tip not in bucket:
                bucket[offer.tip] = offer
            # Legacy: prva non-registracija ponuda
            if key not in offer_map and offer.tip != LiveVisitorOffer.Tip.REGISTRACIJA:
                offer_map[key] = offer
            elif key not in offer_map and offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
                # samo registracija — stavi ako nema druge
                pass
        # Ako nema ništa u offer_map, registracija je fallback
        for key in keys:
            if key not in offer_map:
                offer_map[key] = offer

    gift_push_by_session = {}
    if session_keys:
        for push in (
            OnlineGiftPush.objects.filter(
                session_key__in=session_keys,
                kreirano__gte=offer_cutoff,
            )
            .select_related('campaign', 'campaign__product')
            .order_by('-kreirano')
        ):
            if push.session_key not in gift_push_by_session:
                gift_push_by_session[push.session_key] = push

    gift_claim_by_session = {}
    gift_claim_by_user = {}
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
            if claim.session_key and claim.session_key not in gift_claim_by_session:
                gift_claim_by_session[claim.session_key] = claim
            if claim.user_id and claim.user_id not in gift_claim_by_user:
                gift_claim_by_user[claim.user_id] = claim

    return {
        'offer_map': offer_map,
        'offers_by_key': offers_by_key,
        'gift_push_by_session': gift_push_by_session,
        'gift_claim_by_session': gift_claim_by_session,
        'gift_claim_by_user': gift_claim_by_user,
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
        kind = 'product_offer'
        kind_label = 'Ponuda artikla'
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
        if accepted:
            status = 'accepted'
            status_label = 'Prihvatio — u korpi'
        elif active and visitor_online:
            status = 'active'
            status_label = 'Čeka odgovor'
        else:
            status = 'left'
            status_label = 'Odbio / napustio'

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


def _serialize_gift_staff_action(*, push=None, claim=None, now=None):
    """Online nagrada: push i/ili claim → šta je poslato i šta je kupac uradio."""
    now = now or timezone.now()
    prize = ''
    if claim and claim.won:
        prize = claim.prize_label() if hasattr(claim, 'prize_label') else ''
    if not prize and push and push.campaign_id and push.campaign:
        try:
            prize = push.campaign.prize_label()
        except Exception:
            prize = push.campaign.naziv or 'Online nagrada'
    if not prize:
        prize = 'Online nagrada'

    if claim:
        sent_at = claim.kreirano
        if claim.won:
            if claim.reward_consumed or claim.order_id:
                status, status_label = 'accepted', 'Osvojio i iskoristio'
                if claim.order_id and claim.order:
                    status_label = f'Osvojio · poručio #{claim.order.broj}'
            else:
                status, status_label = 'accepted', 'Osvojio nagradu'
        else:
            status, status_label = 'left', 'Nije dobio (promašaj)'
        source = 'auto' if not push else 'manual'
    elif push:
        sent_at = push.kreirano
        source = 'manual'
        if push.played:
            status, status_label = 'active', 'Otvorio nagradu'
        elif push.dismissed:
            status, status_label = 'left', 'Zatvorio nagradu (X)'
        else:
            status, status_label = 'active', 'Puštana — čeka otvaranje'
    else:
        return None

    return {
        'kind': 'gift',
        'kind_label': 'Online nagrada',
        'title': prize,
        'status': status,
        'status_label': status_label,
        'discount_label': '',
        'product_id': claim.product_id if claim else None,
        'sent_at_label': _ago_action_label(sent_at, now),
        'sent_at_clock': (
            timezone.localtime(sent_at).strftime('%H:%M') if sent_at else ''
        ),
        'already_sent': True,
        'source': source,
    }


def _staff_actions_for_visitor(visitor, actions_bundle, *, visitor_online=False, now=None):
    """Sve poslane staff akcije + ishod za ovog posjetioca."""
    now = now or timezone.now()
    from .models import LiveVisitorOffer

    actions = []
    by_tip = _lookup_offers_by_tip(actions_bundle, visitor)
    # Redoslijed: artikal, popust, registracija
    for tip in (
        LiveVisitorOffer.Tip.ARTIKAL,
        LiveVisitorOffer.Tip.NARUDZBA,
        LiveVisitorOffer.Tip.REGISTRACIJA,
    ):
        offer = by_tip.get(tip)
        if offer:
            action = _serialize_staff_action_from_offer(
                offer, visitor_online=visitor_online, now=now,
            )
            # Ako se gost registrovao nakon poziva — označi kao prihvaćeno
            if (
                tip == LiveVisitorOffer.Tip.REGISTRACIJA
                and visitor.user_id
                and action.get('status') != 'accepted'
            ):
                action['status'] = 'accepted'
                action['status_label'] = 'Registrovao se'
            actions.append(action)

    push = (actions_bundle.get('gift_push_by_session') or {}).get(visitor.session_key)
    claim = None
    if visitor.session_key:
        claim = (actions_bundle.get('gift_claim_by_session') or {}).get(visitor.session_key)
    if not claim and visitor.user_id:
        claim = (actions_bundle.get('gift_claim_by_user') or {}).get(visitor.user_id)
    gift_action = _serialize_gift_staff_action(push=push, claim=claim, now=now)
    if gift_action:
        actions.append(gift_action)

    return actions


def _build_cart_presence_map(visitors):
    """Vraća (sessions_with_cart, users_with_cart, cart_value_by_session, cart_value_by_user)."""
    from decimal import Decimal

    from django.db.models import Sum

    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    sessions_with_cart = set()
    users_with_cart = set()
    cart_value_by_session = {}
    cart_value_by_user = {}

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

    return sessions_with_cart, users_with_cart, cart_value_by_session, cart_value_by_user


def _visitor_has_cart(visitor, sessions_with_cart, users_with_cart):
    if visitor.user_id and visitor.user_id in users_with_cart:
        return True
    return visitor.session_key in sessions_with_cart


def _visitor_cart_value(visitor, cart_value_by_session, cart_value_by_user):
    from decimal import Decimal

    if visitor.user_id and visitor.user_id in cart_value_by_user:
        return cart_value_by_user[visitor.user_id]
    return cart_value_by_session.get(visitor.session_key) or Decimal('0')


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

    categories = list(visitor.pregledane_kategorije or [])
    products = _normalize_product_views(getattr(visitor, 'pregledani_proizvodi', None))
    products_viewed_count = len(products)
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

    payload = {
        'session_key': visitor.session_key,
        'user_id': visitor.user_id or None,
        'ime': visitor.ime or 'Gost',
        'email': visitor.email or '',
        'grad': grad,
        'categories': categories,
        'categories_label': ', '.join(categories),
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