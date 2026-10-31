from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from django import template

register = template.Library()


@register.filter
def dict_get(mapping, key):
    """Dohvati mapping[key] — radi i sa str/int ključevima (dwell flash mapa)."""
    if not mapping or key is None:
        return None
    if key in mapping:
        return mapping[key]
    s = str(key)
    if s in mapping:
        return mapping[s]
    try:
        i = int(key)
    except (TypeError, ValueError):
        return None
    if i in mapping:
        return mapping[i]
    return None


@register.filter
def format_mmss(seconds):
    """Pretvori sekunde u M:SS (za dwell tajmer)."""
    try:
        sec = max(0, int(seconds))
    except (TypeError, ValueError):
        return '0:00'
    m, s = divmod(sec, 60)
    return f'{m}:{s:02d}'


@register.filter
def loyalty_bodovi(cijena):
    """
    Bodovi koje kupac osvaja kupovinom: 1 bod = 1 KM (zaokruženo na cijeli broj).
    Bodove ostvaruju samo registrovani korisnici — ovo je samo izračun za prikaz.
    """
    if cijena is None or cijena == '':
        return 0
    try:
        value = Decimal(str(cijena).replace(',', '.').strip())
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        return 0
    if value <= 0:
        return 0
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


@register.simple_tag
def live_stock_products(products):
    from EcommerceApp.storefront_stock import storefront_products
    return storefront_products(products)


@register.simple_tag
def live_stock_product(product):
    from EcommerceApp.storefront_stock import storefront_products
    return storefront_products([product])[0]


@register.filter
def description_blocks(value):
    """Group short plain-text description lines into readable feature lists."""
    import re
    blocks = []
    for raw in str(value or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        explicit_bullet = bool(re.match(r'^(?:[•●▪✓]|[-*]\s)', line))
        text = re.sub(r'^(?:[•●▪✓]\s*|[-*]\s+)', '', line)
        feature = explicit_bullet or len(text) <= 120
        kind = 'features' if feature else 'paragraph'
        if feature and blocks and blocks[-1]['kind'] == kind:
            blocks[-1]['lines'].append(text)
        else:
            blocks.append({'kind': kind, 'lines': [text]})
    return blocks
