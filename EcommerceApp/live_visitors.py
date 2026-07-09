import calendar
import random
import re
from datetime import datetime, timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate, TruncMonth
from django.utils import timezone

from .models import ActiveCartItem, Category, LiveVisitor, Product
from .visitor_geo import (
    get_client_ip,
    is_known_foreign_visitor,
    resolve_visitor_city,
    resolve_visitor_country,
)

ONLINE_MINUTES = 5
WINDOW_MINUTES = 30
RETENTION_HOURS = 48
BOSNIA_HERZEGOVINA_COUNTRY_CODE = 'BA'
MAX_TRACKED_CATEGORIES = 8



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


def _category_names_from_request(request):
    path = (request.path or '').rstrip('/') or '/'

    category_match = re.match(r'^/kategorija/([^/]+)$', path)
    if category_match:
        category = Category.objects.filter(
            slug=category_match.group(1),
            aktivan=True,
        ).only('naziv').first()
        if category:
            return [category.naziv]

    product_match = re.match(r'^/artikal/([^/]+)$', path)
    if product_match:
        product = Product.objects.filter(
            slug=product_match.group(1),
            aktivan=True,
        ).select_related('kategorija').only('kategorija__naziv').first()
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


def track_live_visitor(request):
    if not should_track_visitor(request):
        return
    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key or ''
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
    existing_categories = list(
        (existing_visitor.pregledane_kategorije if existing_visitor else None) or [],
    )
    new_categories = _category_names_from_request(request)
    if new_categories:
        existing_categories = _merge_category_history(existing_categories, new_categories)

    defaults = {
        'user': user,
        'ime': _display_name(user)[:120],
        'email': _display_email(user)[:254],
        'grad': (grad or '')[:100],
        'drzava': BOSNIA_HERZEGOVINA_COUNTRY_CODE,
        'ip_adresa': ip or None,
        'pregledane_kategorije': existing_categories,
        'last_seen': now,
    }
    LiveVisitor.objects.update_or_create(
        session_key=session_key,
        defaults=defaults,
    )
    if random.random() < 0.02:
        cleanup_stale_live_visitors()


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
    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    sessions_with_cart = set()
    users_with_cart = set()
    if session_keys:
        sessions_with_cart = set(
            ActiveCartItem.objects.filter(session_key__in=session_keys)
            .values_list('session_key', flat=True)
            .distinct(),
        )
    if user_ids:
        users_with_cart = set(
            ActiveCartItem.objects.filter(user_id__in=user_ids)
            .values_list('user_id', flat=True)
            .distinct(),
        )
    return sessions_with_cart, users_with_cart


def _visitor_has_cart(visitor, sessions_with_cart, users_with_cart):
    if visitor.user_id and visitor.user_id in users_with_cart:
        return True
    return visitor.session_key in sessions_with_cart


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


def get_traffic_filter_defaults():
    today = timezone.localdate()
    year = today.year
    month = today.month - 11
    while month <= 0:
        month += 12
        year -= 1
    return {
        'daily_from': (today - timedelta(days=13)).isoformat(),
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
    daily_qs = LiveVisitor.objects.all()
    if daily_from:
        daily_qs = daily_qs.filter(first_seen__date__gte=daily_from)
    if daily_to:
        daily_qs = daily_qs.filter(first_seen__date__lte=daily_to)

    daily_rows = (
        daily_qs.annotate(day=TruncDate('first_seen'))
        .values('day')
        .annotate(count=Count('pk'))
        .order_by('-day')
    )

    monthly_qs = LiveVisitor.objects.all()
    if monthly_from:
        monthly_qs = monthly_qs.filter(first_seen__date__gte=monthly_from)
    if monthly_to:
        monthly_qs = monthly_qs.filter(first_seen__date__lte=_month_end(monthly_to))

    monthly_rows = (
        monthly_qs.annotate(month=TruncMonth('first_seen'))
        .values('month')
        .annotate(count=Count('pk'))
        .order_by('-month')
    )

    daily_stats = []
    for row in daily_rows:
        day = row['day']
        label = day.strftime('%d.%m.%Y.') if day else '—'
        daily_stats.append({'label': label, 'count': row['count']})

    monthly_stats = []
    for row in monthly_rows:
        month = row['month']
        label = month.strftime('%m/%Y') if month else '—'
        monthly_stats.append({'label': label, 'count': row['count']})

    return {
        'daily': daily_stats,
        'monthly': monthly_stats,
    }


def _visitor_payload(visitor, *, now, offer=None, has_cart=False):
    seconds_ago = max(0, int((now - visitor.last_seen).total_seconds()))
    if seconds_ago < 60:
        ago_label = 'upravo sada'
    elif seconds_ago < 3600:
        minutes = seconds_ago // 60
        ago_label = f'prije {minutes} min'
    else:
        hours = seconds_ago // 3600
        ago_label = f'prije {hours} h'
    grad = ''
    if (visitor.drzava or '').strip().upper() == BOSNIA_HERZEGOVINA_COUNTRY_CODE:
        grad = (visitor.grad or '').strip()
        if not grad and visitor.user_id and getattr(visitor, 'user', None):
            profil = getattr(visitor.user, 'profil', None)
            if profil and profil.grad:
                grad = profil.grad.strip()

    categories = list(visitor.pregledane_kategorije or [])
    payload = {
        'session_key': visitor.session_key,
        'ime': visitor.ime or 'Gost',
        'email': visitor.email or '',
        'grad': grad,
        'categories': categories,
        'categories_label': ', '.join(categories),
        'has_cart': has_cart,
        'is_guest': not visitor.user_id and not visitor.email,
        'can_invite_register': not bool(visitor.user_id),
        'last_seen': visitor.last_seen,
        'last_seen_label': ago_label,
        'seconds_ago': seconds_ago,
        'is_online': seconds_ago <= ONLINE_MINUTES * 60,
    }
    payload.update(_offer_status_fields(offer, visitor_online=payload['is_online']))
    return payload


def get_live_visitor_snapshot():
    now = timezone.now()
    online_cutoff = now - timedelta(minutes=ONLINE_MINUTES)
    window_cutoff = now - timedelta(minutes=WINDOW_MINUTES)

    window_qs = LiveVisitor.objects.filter(
        last_seen__gte=window_cutoff,
        drzava=BOSNIA_HERZEGOVINA_COUNTRY_CODE,
    ).select_related('user__profil').order_by('-last_seen')
    visitor_rows = list(window_qs)
    offer_map = _build_recent_offer_map(visitor_rows, now=now)
    sessions_with_cart, users_with_cart = _build_cart_presence_map(visitor_rows)
    window_visitors = [
        _visitor_payload(
            row,
            now=now,
            offer=_lookup_recent_offer(offer_map, row),
            has_cart=_visitor_has_cart(row, sessions_with_cart, users_with_cart),
        )
        for row in visitor_rows
    ]
    online_visitors = [row for row in window_visitors if row['is_online']]

    return {
        'online_count': len(online_visitors),
        'window_count': len(window_visitors),
        'online_visitors': online_visitors,
        'window_visitors': window_visitors,
        'online_minutes': ONLINE_MINUTES,
        'window_minutes': WINDOW_MINUTES,
        'generated_at': now,
    }