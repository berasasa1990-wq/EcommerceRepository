import re

from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()


@register.filter
def product_description(value):
    """Render plain description text as escaped paragraphs and feature lists."""
    blocks = []
    items = []
    listing = False
    headings = {'značajke', 'karakteristike', 'specifikacije', 'u kutiji', 'jamstvo'}

    def flush_items():
        if items:
            blocks.append(format_html('<ul>{}</ul>', format_html_join('', '<li>{}</li>', ((item,) for item in items))))
            items.clear()

    for raw in str(value or '').splitlines():
        line = raw.strip()
        if not line:
            flush_items()
            continue
        heading = line.rstrip(':').casefold()
        if heading == 'opis':
            flush_items()
            listing = False
            continue
        if heading in headings:
            flush_items()
            blocks.append(format_html('<h3>{}</h3>', line.rstrip(':')))
            listing = True
            continue
        bullet = re.match(r'^(?:[-*•●]\s+|\d+[.)]\s+)(.+)$', line)
        if listing or bullet:
            items.append(bullet.group(1) if bullet else line)
        else:
            flush_items()
            blocks.append(format_html('<p>{}</p>', line))
    flush_items()
    return format_html_join('', '{}', ((block,) for block in blocks))
