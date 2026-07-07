from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import ActiveCartItem, Product, ProductVariation

STALE_CART_DAYS = 14


def get_cart_session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


def _cart_user(request):
    if getattr(request, 'user', None) and request.user.is_authenticated:
        return request.user
    return None


def _line_defaults(request, item):
    product = Product.objects.filter(pk=item.get('product_id')).first()
    variation = None
    variation_id = item.get('variation_id')
    if variation_id:
        variation = ProductVariation.objects.filter(pk=variation_id).first()
    quantity = max(1, int(item.get('quantity', 1) or 1))
    price = Decimal(str(item.get('cijena', 0)))
    return {
        'user': _cart_user(request),
        'product': product,
        'variation': variation,
        'naziv': item.get('product_naziv') or item.get('naziv', ''),
        'varijacija_naziv': item.get('varijacija_naziv', ''),
        'kolicina': quantity,
        'cijena': price,
        'ukupno': (price * quantity).quantize(Decimal('0.01')),
    }


def _attach_user_to_session(request, session_key):
    user = _cart_user(request)
    if user and session_key:
        ActiveCartItem.objects.filter(session_key=session_key).update(user=user)


def track_cart_line_added_or_updated(request, key, item):
    session_key = get_cart_session_key(request)
    if not session_key or not item:
        return
    defaults = _line_defaults(request, item)
    if not defaults['product']:
        return
    ActiveCartItem.objects.update_or_create(
        session_key=session_key,
        line_key=key,
        defaults=defaults,
    )
    _attach_user_to_session(request, session_key)


def track_cart_line_removed(request, key):
    session_key = get_cart_session_key(request)
    if session_key:
        ActiveCartItem.objects.filter(session_key=session_key, line_key=key).delete()


def track_cart_cleared(request):
    session_key = get_cart_session_key(request)
    if session_key:
        ActiveCartItem.objects.filter(session_key=session_key).delete()


def sync_active_cart(request, cart):
    """Potpuna usklađenost sesije i baze (npr. pri otvaranju korpe)."""
    session_key = get_cart_session_key(request)
    if not session_key:
        return

    current_keys = set()
    user = _cart_user(request)
    for key, item in cart.cart.items():
        current_keys.add(key)
        defaults = _line_defaults(request, item)
        if not defaults['product']:
            continue
        defaults['user'] = user
        ActiveCartItem.objects.update_or_create(
            session_key=session_key,
            line_key=key,
            defaults=defaults,
        )

    ActiveCartItem.objects.filter(session_key=session_key).exclude(
        line_key__in=current_keys,
    ).delete()


def cleanup_stale_active_cart_items():
    cutoff = timezone.now() - timedelta(days=STALE_CART_DAYS)
    return ActiveCartItem.objects.filter(azurirano__lt=cutoff).delete()[0]