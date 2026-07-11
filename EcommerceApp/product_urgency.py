"""
Urgency / social-proof brojevi na stranici proizvoda.
Stabilni tokom dana (isti artikal = isti broj do ponoći).
"""
import hashlib
from datetime import date


def _day_seed(product_id):
    day = date.today().isoformat()
    raw = f'urgency:{product_id}:{day}'.encode('utf-8')
    return int(hashlib.md5(raw).hexdigest(), 16)


def _product_has_stock(product):
    if getattr(product, 'na_stanju', False):
        return True
    variations = getattr(product, 'varijacije', None)
    if variations is None:
        return False
    try:
        return any(v.na_stanju for v in variations.all())
    except Exception:
        return False


def build_product_urgency(product):
    """
    Vraća:
      sold_24h   — 2..10 (svi artikli na stanju)
      stock_left — 2..4 samo za akcijske (katalog_na_akciji / na_akciji)
      show_stock — bool
    """
    if not product or not getattr(product, 'pk', None):
        return None

    seed = _day_seed(product.pk)
    sold_24h = 2 + (seed % 9)  # 2–10
    stock_left = 2 + ((seed // 9) % 3)  # 2–4

    on_sale = bool(
        getattr(product, 'katalog_na_akciji', False)
        or getattr(product, 'na_akciji', False)
    )
    has_stock = _product_has_stock(product)
    show_stock = on_sale and has_stock

    return {
        'sold_24h': sold_24h,
        'stock_left': stock_left if show_stock else None,
        'show_stock': show_stock,
        'on_sale': on_sale,
    }
