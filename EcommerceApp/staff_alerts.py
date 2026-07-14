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


def notify_visitor_online(*, ime='', email='', grad='', session_key='', trenutno_gleda=''):
    label = _actor_label(ime=ime, email=email, grad=grad)
    page = (trenutno_gleda or '').strip()
    if page:
        poruka = f'{label} je na sajtu — sada: {page}.'
    else:
        poruka = f'{label} je na sajtu.'
    return push_staff_event(
        StaffSiteEvent.Tip.ONLINE,
        naslov='Kupac na sajtu',
        poruka=poruka,
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
    # Strukturirani rep u poruci radi celebration popupa (order_number / total)
    meta = []
    if order_number:
        meta.append(f'ORDER:{order_number}')
    if total:
        meta.append(f'TOTAL:{total}')
    poruka = ' '.join(parts) + '.'
    if meta:
        poruka = poruka + ' [[' + '|'.join(meta) + ']]'
    return push_staff_event(
        StaffSiteEvent.Tip.PURCHASE,
        naslov='Nova kupovina',
        poruka=poruka,
        ime=ime,
        email=email,
        grad=grad,
        session_key=session_key,
    )


def notify_offer_accepted(
    *,
    ime='',
    email='',
    grad='',
    session_key='',
    product_name='',
    discount_percent=None,
    source='ponuda',
):
    """Kupac je prihvatio AI prodaju / staff ponudu — toast superuserima."""
    label = _actor_label(ime=ime, email=email, grad=grad)
    product = (product_name or '').strip() or 'artikal'
    pct = ''
    try:
        if discount_percent is not None:
            d = float(discount_percent)
            if d > 0:
                pct = f' (−{int(d) if d == int(d) else d}%)'
    except (TypeError, ValueError):
        pct = ''
    src = (source or 'ponuda').strip()
    return push_staff_event(
        StaffSiteEvent.Tip.OFFER,
        naslov='Prihvaćena ponuda',
        poruka=f'{label} je prihvatio/la {src}: {product}{pct}.',
        ime=ime,
        email=email,
        grad=grad,
        session_key=session_key,
    )


def count_new_online_orders():
    from .models import Order

    return Order.objects.filter(status=Order.Status.NOVA).count()


def cleanup_staff_events():
    cutoff = timezone.now() - timedelta(hours=RETENTION_HOURS)
    return StaffSiteEvent.objects.filter(kreirano__lt=cutoff).delete()[0]


def _online_session_keys():
    """
    Session keys posjetilaca online za sticky toast.
    Poštuje leave cache (odmah offline) + kratki last_seen prozor.
    """
    from .live_visitors import (
        STAFF_TOAST_ONLINE_SECONDS,
        is_visitor_marked_left,
    )
    from .models import LiveVisitor

    cutoff = timezone.now() - timedelta(seconds=STAFF_TOAST_ONLINE_SECONDS)
    candidates = list(
        LiveVisitor.objects.filter(last_seen__gte=cutoff)
        .exclude(session_key='')
        .values_list('session_key', flat=True)
    )
    online = []
    for session_key in candidates:
        if is_visitor_marked_left(session_key):
            continue
        online.append(session_key)
    return online


def _format_money(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    text = f'{amount:.2f}'.replace('.', ',')
    return text


def _offer_reject_flags(offer):
    """Da li je posjetilac odbio ponudu / poziv na registraciju."""
    from .models import LiveVisitorOffer

    flags = {
        'offer_rejected': False,
        'register_rejected': False,
        'offer_active': False,
        'register_active': False,
        'offer_accepted': False,
    }
    if not offer:
        return flags

    if offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
        flags['register_active'] = bool(offer.show_popup)
        flags['register_rejected'] = not bool(offer.show_popup)
        return flags

    accepted = bool(offer.added_to_cart) or bool(offer.kod_aktiviran)
    flags['offer_accepted'] = accepted
    flags['offer_active'] = bool(offer.show_popup) and not accepted
    flags['offer_rejected'] = (not bool(offer.show_popup)) and not accepted
    return flags


def build_visitor_states(session_keys):
    """
    Stanje online posjetilaca za sticky toast:
    kupac, korpa, odbijena ponuda / registracija.
    """
    from collections import defaultdict
    from decimal import Decimal

    from .live_visitors import (
        _build_site_buyer_sets,
        _visitor_has_purchased,
    )
    from .models import ActiveCartItem, LiveVisitor, LiveVisitorOffer

    keys = [k for k in (session_keys or []) if k]
    if not keys:
        return {}

    visitors = list(
        LiveVisitor.objects.filter(session_key__in=keys).select_related('user'),
    )
    visitor_by_key = {v.session_key: v for v in visitors}
    buyer_user_ids, buyer_emails = _build_site_buyer_sets(visitors)

    cart_rows = (
        ActiveCartItem.objects.filter(session_key__in=keys)
        .order_by('-azurirano', '-id')
    )
    carts = defaultdict(list)
    cart_totals = defaultdict(lambda: Decimal('0'))
    for row in cart_rows:
        label = (row.naziv or '').strip() or 'Artikal'
        if row.varijacija_naziv:
            label = f'{label} — {row.varijacija_naziv}'
        qty = int(row.kolicina or 1)
        line_total = row.ukupno if row.ukupno is not None else (row.cijena or 0) * qty
        carts[row.session_key].append({
            'name': label[:120],
            'qty': qty,
            'price': _format_money(row.cijena),
            'total': _format_money(line_total),
        })
        try:
            cart_totals[row.session_key] += Decimal(str(line_total or 0))
        except Exception:
            pass

    offers = (
        LiveVisitorOffer.objects.filter(session_key__in=keys)
        .order_by('-azurirano', '-id')
    )
    offer_by_key = {}
    for offer in offers:
        if offer.session_key not in offer_by_key:
            offer_by_key[offer.session_key] = offer

    states = {}
    for session_key in keys:
        visitor = visitor_by_key.get(session_key)
        offer = offer_by_key.get(session_key)
        flags = _offer_reject_flags(offer)
        cart_items = carts.get(session_key) or []
        has_user = bool(visitor and visitor.user_id)
        states[session_key] = {
            'session_key': session_key,
            'ime': (visitor.ime if visitor else '') or 'Gost',
            'email': (visitor.email if visitor else '') or '',
            'grad': (visitor.grad if visitor else '') or '',
            'has_purchased': bool(
                visitor and _visitor_has_purchased(visitor, buyer_user_ids, buyer_emails),
            ),
            'can_register': not has_user,
            'can_offer': True,
            'offer_rejected': flags['offer_rejected'],
            'register_rejected': flags['register_rejected'],
            'offer_active': flags['offer_active'],
            'register_active': flags['register_active'],
            'offer_accepted': flags['offer_accepted'],
            'cart_items': cart_items,
            'cart_count': len(cart_items),
            'cart_total': _format_money(cart_totals.get(session_key, 0)),
        }
    return states


def get_staff_events_since(since_id=0, *, limit=MAX_EVENTS_RETURN):
    try:
        since_id = int(since_id or 0)
    except (TypeError, ValueError):
        since_id = 0

    online_sessions = _online_session_keys()
    visitor_states = build_visitor_states(online_sessions)
    new_orders_count = count_new_online_orders()

    qs = StaffSiteEvent.objects.all().order_by('id')
    if since_id > 0:
        qs = qs.filter(id__gt=since_id)
    else:
        # Prvi poll: samo zadnji ID da ne poplavi starim događajima
        latest = StaffSiteEvent.objects.order_by('-id').values_list('id', flat=True).first()
        return {
            'events': [],
            'latest_id': latest or 0,
            'online_sessions': online_sessions,
            'visitor_states': visitor_states,
            'new_orders_count': new_orders_count,
        }

    events = list(qs[: max(1, min(int(limit or MAX_EVENTS_RETURN), 50))])
    latest_id = events[-1].id if events else since_id

    event_session_keys = [e.session_key for e in events if e.session_key]
    registered_sessions = set()
    if event_session_keys:
        from .models import LiveVisitor
        registered_sessions = set(
            LiveVisitor.objects.filter(
                session_key__in=event_session_keys,
                user_id__isnull=False,
            ).values_list('session_key', flat=True)
        )

    # Dopuni state i za sesije iz novih događaja (npr. korpa)
    missing = [k for k in event_session_keys if k not in visitor_states]
    if missing:
        visitor_states.update(build_visitor_states(missing))

    online_set = set(online_sessions)
    payload = []
    for event in events:
        session_key = event.session_key or ''
        state = visitor_states.get(session_key) or {}
        can_act = bool(session_key) and event.tip in {
            StaffSiteEvent.Tip.ONLINE,
            StaffSiteEvent.Tip.CART,
        }
        sticky = (
            event.tip in {StaffSiteEvent.Tip.ONLINE, StaffSiteEvent.Tip.CART}
            and bool(session_key)
            and session_key in online_set
        )
        can_register = can_act and session_key not in registered_sessions
        if state:
            can_register = bool(state.get('can_register', can_register))
        raw_poruka = event.poruka or ''
        order_number = ''
        order_total = ''
        display_poruka = raw_poruka
        if '[[' in raw_poruka and ']]' in raw_poruka:
            try:
                main, meta = raw_poruka.rsplit('[[', 1)
                display_poruka = main.strip()
                meta = meta.replace(']]', '')
                for part in meta.split('|'):
                    if part.startswith('ORDER:'):
                        order_number = part[6:].strip()
                    elif part.startswith('TOTAL:'):
                        order_total = part[6:].strip()
            except Exception:
                display_poruka = raw_poruka
        if not order_number:
            # fallback: (#BROJ) u tekstu
            import re
            m = re.search(r'#([A-Za-z0-9\-]+)', raw_poruka)
            if m:
                order_number = m.group(1)

        payload.append({
            'id': event.id,
            'tip': event.tip,
            'naslov': event.naslov,
            'poruka': display_poruka,
            'ime': event.ime or state.get('ime') or '',
            'email': event.email or state.get('email') or '',
            'grad': event.grad or state.get('grad') or '',
            'session_key': session_key,
            'order_number': order_number,
            'order_total': order_total,
            'can_register': can_register,
            'can_offer': can_act or bool(state.get('can_offer')),
            'sticky': sticky,
            'has_purchased': bool(state.get('has_purchased')),
            'offer_rejected': bool(state.get('offer_rejected')),
            'register_rejected': bool(state.get('register_rejected')),
            'offer_active': bool(state.get('offer_active')),
            'register_active': bool(state.get('register_active')),
            'cart_items': state.get('cart_items') or [],
            'cart_count': state.get('cart_count') or 0,
            'cart_total': state.get('cart_total') or '0,00',
            'kreirano': timezone.localtime(event.kreirano).strftime('%H:%M:%S'),
        })
    return {
        'events': payload,
        'latest_id': latest_id,
        'online_sessions': online_sessions,
        'visitor_states': visitor_states,
        'new_orders_count': new_orders_count,
    }
