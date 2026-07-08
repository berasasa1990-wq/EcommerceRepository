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


def _visitor_payload(visitor, *, now):
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

    return {
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


def get_live_visitor_snapshot():
    now = timezone.now()
    online_cutoff = now - timedelta(minutes=ONLINE_MINUTES)
    window_cutoff = now - timedelta(minutes=WINDOW_MINUTES)

    window_qs = LiveVisitor.objects.filter(
        last_seen__gte=window_cutoff,
        drzava=BOSNIA_HERZEGOVINA_COUNTRY_CODE,
    ).order_by('-last_seen')
    window_visitors = [_visitor_payload(row, now=now) for row in window_qs]
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