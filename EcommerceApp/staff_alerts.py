"""Live obavijesti za superusere dok su na sajtu."""
from __future__ import annotations

import random
from datetime import timedelta

from django.utils import timezone

from .models import StaffSiteEvent

RETENTION_HOURS = 24
MAX_EVENTS_RETURN = 20


def _actor_label(*, ime='', email='', grad=''):
    name = (ime or '').strip() or (email or '').strip() or 'Gost'
    city = (grad or '').strip()
    if city:
        return f'{name} ({city})'
    return name


def push_staff_event(
    tip,
    *,
    naslov,
    poruka='',
    ime='',
    email='',
    grad='',
    session_key='',
):
    """Snimi događaj koji superuseri vide kao toast na sajtu."""
    tip = (tip or '').strip()
    if tip not in {choice.value for choice in StaffSiteEvent.Tip}:
        return None

    event = StaffSiteEvent.objects.create(
        tip=tip,
        naslov=(naslov or StaffSiteEvent.Tip(tip).label)[:120],
        poruka=(poruka or '')[:300],
        ime=(ime or '')[:120],
        email=(email or '')[:254],
        grad=(grad or '')[:100],
        session_key=(session_key or '')[:40],
    )
    if random.random() < 0.05:
        cleanup_staff_events()
    return event


def notify_visitor_online(*, ime='', email='', grad='', session_key=''):
    label = _actor_label(ime=ime, email=email, grad=grad)
    return push_staff_event(
        StaffSiteEvent.Tip.ONLINE,
        naslov='Novi posjetilac online',
        poruka=f'{label} je na sajtu.',
        ime=ime,
        email=email,
        grad=grad,
        session_key=session_key,
    )


def notify_cart_add(*, ime='', email='', grad='', session_key='', product_name=''):
    label = _actor_label(ime=ime, email=email, grad=grad)
    product = (product_name or '').strip()
    detail = f'{label} je dodao/la u korpu: {product}.' if product else f'{label} je dodao/la artikal u korpu.'
    return push_staff_event(
        StaffSiteEvent.Tip.CART,
        naslov='Dodano u korpu',
        poruka=detail,
        ime=ime,
        email=email,
        grad=grad,
        session_key=session_key,
    )


def notify_registration(*, ime='', email='', grad='', session_key=''):
    label = _actor_label(ime=ime, email=email, grad=grad)
    return push_staff_event(
        StaffSiteEvent.Tip.REGISTER,
        naslov='Nova registracija',
        poruka=f'{label} se registrovao/la.',
        ime=ime,
        email=email,
        grad=grad,
        session_key=session_key,
    )


def notify_purchase(*, ime='', email='', grad='', session_key='', order_number='', total=''):
    label = _actor_label(ime=ime, email=email, grad=grad)
    parts = [f'{label} je kupio/la preko sajta']
    if order_number:
        parts.append(f'(#{order_number})')
    if total:
        parts.append(f'— {total} KM')
    return push_staff_event(
        StaffSiteEvent.Tip.PURCHASE,
        naslov='Nova kupovina',
        poruka=' '.join(parts) + '.',
        ime=ime,
        email=email,
        grad=grad,
        session_key=session_key,
    )


def cleanup_staff_events():
    cutoff = timezone.now() - timedelta(hours=RETENTION_HOURS)
    return StaffSiteEvent.objects.filter(kreirano__lt=cutoff).delete()[0]


def get_staff_events_since(since_id=0, *, limit=MAX_EVENTS_RETURN):
    try:
        since_id = int(since_id or 0)
    except (TypeError, ValueError):
        since_id = 0
    qs = StaffSiteEvent.objects.all().order_by('id')
    if since_id > 0:
        qs = qs.filter(id__gt=since_id)
    else:
        # Prvi poll: samo zadnji ID da ne poplavi starim događajima
        latest = StaffSiteEvent.objects.order_by('-id').values_list('id', flat=True).first()
        return {
            'events': [],
            'latest_id': latest or 0,
        }

    events = list(qs[: max(1, min(int(limit or MAX_EVENTS_RETURN), 50))])
    latest_id = events[-1].id if events else since_id

    session_keys = [e.session_key for e in events if e.session_key]
    registered_sessions = set()
    if session_keys:
        from .models import LiveVisitor
        registered_sessions = set(
            LiveVisitor.objects.filter(
                session_key__in=session_keys,
                user_id__isnull=False,
            ).values_list('session_key', flat=True)
        )

    payload = []
    for event in events:
        session_key = event.session_key or ''
        can_act = bool(session_key) and event.tip in {
            StaffSiteEvent.Tip.ONLINE,
            StaffSiteEvent.Tip.CART,
        }
        payload.append({
            'id': event.id,
            'tip': event.tip,
            'naslov': event.naslov,
            'poruka': event.poruka,
            'ime': event.ime,
            'email': event.email,
            'grad': event.grad,
            'session_key': session_key,
            'can_register': can_act and session_key not in registered_sessions,
            'can_offer': can_act,
            'kreirano': timezone.localtime(event.kreirano).strftime('%H:%M:%S'),
        })
    return {
        'events': payload,
        'latest_id': latest_id,
    }
