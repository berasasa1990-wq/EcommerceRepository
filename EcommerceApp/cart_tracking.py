import logging
import time
from datetime import timedelta
from decimal import Decimal

from django.db import OperationalError, transaction
from django.utils import timezone

from .models import ActiveCartItem, Product, ProductVariation

logger = logging.getLogger(__name__)

STALE_CART_DAYS = 14
_SQLITE_LOCK_RETRIES = 4
_SQLITE_LOCK_SLEEP = 0.05


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
    # Retired live cart tracking; the customer session cart is unchanged.
    """Retired module: preserve stored history without background work."""
    return None


def track_cart_line_removed(request, key):
    # Retired live cart tracking; the customer session cart is unchanged.
    """Retired module: preserve stored history without background work."""
    return None


def track_cart_cleared(request):
    # Retired live cart tracking; the customer session cart is unchanged.
    """Retired module: preserve stored history without background work."""
    return None


def _is_db_locked(exc):
    return isinstance(exc, OperationalError) and 'locked' in str(exc).lower()


def sync_active_cart(request, cart):
    # Retired live cart tracking; the customer session cart is unchanged.
    """Retired module: preserve stored history without background work."""
    return None


def cleanup_stale_active_cart_items():
    # Retired live cart tracking; the customer session cart is unchanged.
    """Retired module: preserve stored history without background work."""
    return None
