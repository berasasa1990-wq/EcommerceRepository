"""Retired Almost Cart API. Historical LiveVisitor records remain untouched."""


def get_almost_cart_products(visitor):
    return []


def top_almost_cart_product_id(visitor):
    return None


def record_almost_cart(request, product_id, *, product_name='', clicked=False):
    """Compatibility no-op: never create sessions, query visitors, or record hover/click events."""
    return None
