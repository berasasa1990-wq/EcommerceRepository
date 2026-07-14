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
