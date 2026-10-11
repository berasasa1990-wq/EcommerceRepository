"""Read-only BERA order preview; never sends mail."""
from django.conf import settings
from django.urls import reverse
from .models import SiteSettings
from .emails import get_order_email_context


def _order_visual_context(order, *, customer=False):
    from urllib.parse import urljoin
    from .pricing import order_paid_by_card
    context = get_order_email_context(order)
    base = settings.SITE_URL.rstrip('/') + '/'
    site_settings = SiteSettings.load()
    if site_settings.logo:
        context['logo_url'] = urljoin(base, site_settings.logo.url)
    source_items = [item for item in order.stavke.select_related('artikal', 'varijacija').all() if item.kolicina_faktura > 0]
    for row, item in zip(context['stavke'], source_items):
        image = getattr(item.varijacija, 'slika', None) or (item.artikal.prikazna_slika if item.artikal else None)
        row['image_url'] = urljoin(base, image.url) if image else ''
        row['unit_charged'] = row['ukupno'] / row['kolicina']
    context['goods_after_discount'] = context['summary']['medjuzbir'] - context['summary']['popust']
    context['admin_order_url'] = urljoin(base, reverse('staff_order_detail', args=[order.broj])) + '?preview=email'
    context['customer_order_url'] = urljoin(base, reverse('account_order_detail', args=[order.broj]))
    context['paid_by_card'] = order_paid_by_card(order)
    context['customer_email'] = customer
    context['email_icon_base'] = urljoin(base, 'static/bera_reference/img/email/')
    return context
