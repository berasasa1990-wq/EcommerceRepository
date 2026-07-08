import random
from datetime import timedelta

from django.utils import timezone

from .models import LiveVisitor
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

    grad = ''
    if not country or country == BOSNIA_HERZEGOVINA_COUNTRY_CODE:
        grad = resolve_visitor_city(request, ip=ip) or ''

    defaults = {
        'user': user,
        'ime': _display_name(user)[:120],
        'email': _display_email(user)[:254],
        'grad': (grad or '')[:100],
        'drzava': BOSNIA_HERZEGOVINA_COUNTRY_CODE,
        'ip_adresa': ip or None,
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

    from .live_visitor_offer import OFFER_TIMER_MINUTES
    from .models import LiveVisitorOffer

    session_keys = [visitor.session_key for visitor in visitors if visitor.session_key]
    user_ids = [visitor.user_id for visitor in visitors if visitor.user_id]
    if not session_keys and not user_ids:
        return {}

    timer_cutoff = now - timedelta(minutes=OFFER_TIMER_MINUTES)
    clauses = Q()
    if session_keys:
        clauses |= Q(session_key__in=session_keys)
    if user_ids:
        clauses |= Q(user_id__in=user_ids)

    offers = (
        LiveVisitorOffer.objects.filter(clauses, azurirano__gte=timer_cutoff)
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


def _offer_status_fields(offer):
    if not offer:
        return {
            'offer_sent': False,
            'offer_active': False,
            'offer_product': '',
            'offer_status': '',
        }

    active = offer.show_popup and not offer.added_to_cart
    if active:
        status = 'active'
    elif offer.added_to_cart:
        status = 'accepted'
    else:
        status = 'dismissed'

    product_name = ''
    if offer.product_id and offer.product:
        product_name = offer.product.naziv

    return {
        'offer_sent': True,
        'offer_active': active,
        'offer_product': product_name,
        'offer_status': status,
    }


def _visitor_payload(visitor, *, now, offer=None):
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
        grad = visitor.grad or ''

    payload = {
        'session_key': visitor.session_key,
        'ime': visitor.ime or 'Gost',
        'email': visitor.email or '',
        'grad': grad,
        'is_guest': not visitor.user_id and not visitor.email,
        'last_seen': visitor.last_seen,
        'last_seen_label': ago_label,
        'seconds_ago': seconds_ago,
        'is_online': seconds_ago <= ONLINE_MINUTES * 60,
    }
    payload.update(_offer_status_fields(offer))
    return payload


def get_live_visitor_snapshot():
    now = timezone.now()
    online_cutoff = now - timedelta(minutes=ONLINE_MINUTES)
    window_cutoff = now - timedelta(minutes=WINDOW_MINUTES)

    window_qs = LiveVisitor.objects.filter(
        last_seen__gte=window_cutoff,
        drzava=BOSNIA_HERZEGOVINA_COUNTRY_CODE,
    ).order_by('-last_seen')
    visitor_rows = list(window_qs)
    offer_map = _build_recent_offer_map(visitor_rows, now=now)
    window_visitors = [
        _visitor_payload(row, now=now, offer=_lookup_recent_offer(offer_map, row))
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