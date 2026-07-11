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

ONLINE_MINUTES = 5
# Staff toast: kraći prozor + heartbeat/leave, da se popup skloni čim kupac ode
STAFF_TOAST_ONLINE_SECONDS = 30
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
    # Superuser obavijest: novi posjetilac online (ne šalji za superusere)
    if created and not (user and user.is_superuser):
        try:
            from .staff_alerts import notify_visitor_online
            notify_visitor_online(
                ime=defaults.get('ime') or '',
                email=defaults.get('email') or '',
                grad=city_name,
                session_key=session_key,
            )
        except Exception:
            pass
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


def _build_recent_offer_map(visitors, *, now):
    from django.db.models import Q

    from .models import LiveVisitorOffer

    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    if not session_keys and not user_ids:
        return {}

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
    for offer in offers:
        if offer.user_id:
            key = ('user', offer.user_id)
            if key not in offer_map:
                offer_map[key] = offer
        if offer.session_key:
            key = ('session', offer.session_key)
            if key not in offer_map:
                offer_map[key] = offer
    return offer_map


def _lookup_recent_offer(offer_map, visitor):
    if visitor.user_id:
        offer = offer_map.get(('user', visitor.user_id))
        if offer:
            return offer
    return offer_map.get(('session', visitor.session_key))


def _offer_status_fields(offer, *, visitor_online=False):
    if not offer:
        return {
            'offer_sent': False,
            'offer_active': False,
            'offer_product': '',
            'offer_status': '',
        }

    from .models import LiveVisitorOffer

    if offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
        accepted = False
        active = bool(offer.show_popup)
        product_name = 'Poziv na registraciju'
    elif offer.tip == LiveVisitorOffer.Tip.NARUDZBA:
        accepted = bool(offer.kod_aktiviran)
        active = offer.show_popup and not accepted
        product_name = f'Popust {offer.discount_percent}%'
        if offer.aktivacioni_kod:
            product_name = f'{product_name} ({offer.aktivacioni_kod})'
    else:
        accepted = bool(offer.added_to_cart)
        active = offer.show_popup and not accepted
        product_name = ''
        if offer.product_id and offer.product:
            product_name = offer.product.naziv

    if accepted:
        status = 'accepted'
    elif active and visitor_online:
        status = 'active'
    else:
        status = 'left'

    return {
        'offer_sent': True,
        'offer_active': status == 'active',
        'offer_product': product_name,
        'offer_status': status,
    }


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

    is_online = seconds_ago <= ONLINE_MINUTES * 60
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
    return payload


def get_live_visitor_snapshot():
    now = timezone.now()
    window_cutoff = now - timedelta(minutes=WINDOW_MINUTES)

    window_qs = LiveVisitor.objects.filter(
        last_seen__gte=window_cutoff,
        drzava=BOSNIA_HERZEGOVINA_COUNTRY_CODE,
    ).select_related('user__profil').order_by('-last_seen')
    visitor_rows = list(window_qs)
    offer_map = _build_recent_offer_map(visitor_rows, now=now)
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
        window_visitors.append(
            _visitor_payload(
                row,
                now=now,
                offer=_lookup_recent_offer(offer_map, row),
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