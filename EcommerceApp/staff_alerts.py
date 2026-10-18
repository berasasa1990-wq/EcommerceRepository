"""Retained purchase and offer event evidence; no live polling."""
from __future__ import annotations

from .models import StaffSiteEvent


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
    return event


def notify_visitor_online(*, ime='', email='', grad='', session_key='', trenutno_gleda=''):
    """
    Onemogućeno — ne šaljemo toast samo jer je kupac online.
    (Uživo analitika i dalje prati posjetioce.)
    """
    return None


def notify_cart_add(*, ime='', email='', grad='', session_key='', product_name=''):
    """Isključeno — live toast samo za narudžbu, ne za korpu."""
    return None


def notify_registration(*, ime='', email='', grad='', session_key=''):
    """Isključeno — live toast samo za narudžbu, ne za registraciju."""
    return None


def notify_purchase(*, ime='', email='', grad='', session_key='', order_number='', total='', shipping=''):
    label = _actor_label(ime=ime, email=email, grad=grad)
    parts = [f'{label} je kupio/la preko sajta']
    if order_number:
        parts.append(f'(#{order_number})')
    total_fmt = _format_money(total) if total not in (None, '') else ''
    if total_fmt:
        parts.append(f'— {total_fmt} KM')
    # Strukturirani rep u poruci radi celebration popupa (order_number / total)
    meta = []
    if order_number:
        meta.append(f'ORDER:{order_number}')
    if total_fmt:
        meta.append(f'TOTAL:{total_fmt}')
    ship = (shipping or '').strip()[:80]
    if ship:
        meta.append(f'SHIP:{ship}')
    poruka = ' '.join(parts) + '.'
    if meta:
        suffix = ' [[' + '|'.join(meta) + ']]'
        max_main = 300 - len(suffix)
        if max_main < 8:
            poruka = suffix[:300]
        else:
            if len(poruka) > max_main:
                poruka = poruka[: max_main - 1] + '…'
            poruka = poruka + suffix
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


def cleanup_staff_events():
    """Retain existing event evidence until explicitly deleted by an administrator."""
    return 0


def _format_money(value):
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    text = f'{amount:.2f}'.replace('.', ',')
    return text
