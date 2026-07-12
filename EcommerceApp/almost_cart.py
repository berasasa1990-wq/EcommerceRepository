"""
Praćenje „skoro dodao u korpu”:
kursor na dugmetu Dodaj u korpu, ali bez klika → najjači intent.
"""

from __future__ import annotations

from django.utils import timezone

from .cart_tracking import get_cart_session_key
from .models import LiveVisitor, Product

MAX_ALMOST_CART = 12
MIN_HOVER_MS = 350


def _normalize_almost(raw):
    items = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if not pid:
            continue
        try:
            hovers = max(1, int(item.get('hovers') or 1))
        except (TypeError, ValueError):
            hovers = 1
        items.append({
            'id': pid,
            'naziv': str(item.get('naziv') or '')[:120],
            'hovers': hovers,
            'last_at': str(item.get('last_at') or '')[:40],
        })
    return items


def get_almost_cart_products(visitor):
    """Sortirano po snazi intenta (više hovera prvo; noviji last_at prvi)."""
    items = _normalize_almost(getattr(visitor, 'skoro_korpa', None))
    # last_at ISO: reverse=True stavlja novije ispred pri istom broju hovera
    return sorted(
        items,
        key=lambda x: (x.get('hovers') or 1, x.get('last_at') or ''),
        reverse=True,
    )


def top_almost_cart_product_id(visitor):
    items = get_almost_cart_products(visitor)
    if not items:
        return None
    return items[0].get('id')


def record_almost_cart(request, product_id, *, product_name='', clicked=False):
    """
    Hover (clicked=False) — evidentiraj.
    Click (clicked=True) — ukloni iz skoro_korpa (dodao / namjeravao klik).
    """
    session_key = get_cart_session_key(request)
    if not session_key:
        return None
    try:
        product_id = int(product_id)
    except (TypeError, ValueError):
        return None
    if product_id <= 0:
        return None

    visitor = LiveVisitor.objects.filter(session_key=session_key).only(
        'pk', 'skoro_korpa',
    ).first()
    if not visitor:
        # Nema još LiveVisitor reda — kreiraj minimalni
        now = timezone.now()
        visitor = LiveVisitor.objects.create(
            session_key=session_key,
            last_seen=now,
            skoro_korpa=[],
            drzava='BA',
        )

    history = _normalize_almost(visitor.skoro_korpa)

    if clicked:
        history = [h for h in history if h['id'] != product_id]
        LiveVisitor.objects.filter(pk=visitor.pk).update(skoro_korpa=history[:MAX_ALMOST_CART])
        return history

    product = Product.objects.filter(pk=product_id).only('pk', 'naziv').first()
    naziv = (product_name or (product.naziv if product else '') or '')[:120]
    now_iso = timezone.now().isoformat()

    matched = None
    for entry in history:
        if entry['id'] == product_id:
            matched = entry
            break
    if matched:
        matched['hovers'] = matched.get('hovers', 1) + 1
        matched['naziv'] = naziv or matched.get('naziv') or ''
        matched['last_at'] = now_iso
        history.remove(matched)
        history.insert(0, matched)
    else:
        history.insert(0, {
            'id': product_id,
            'naziv': naziv,
            'hovers': 1,
            'last_at': now_iso,
        })

    history = history[:MAX_ALMOST_CART]
    LiveVisitor.objects.filter(pk=visitor.pk).update(skoro_korpa=history)
    return history
