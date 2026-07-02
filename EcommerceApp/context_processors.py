import re
from urllib.parse import quote

from django.conf import settings
from django.db.models import Prefetch

from .cart import Cart
from .models import Category, Popup, SiteSettings
from .upsell import get_active_upsell_offer

_CONTACT_MESSAGE = 'Zdravo, imam pitanje sa opremazaribolov.ba'


def _whatsapp_contact_url(phone):
    digits = re.sub(r'\D', '', phone or '')
    if not digits:
        return ''
    if digits.startswith('00'):
        digits = digits[2:]
    return f'https://wa.me/{digits}?text={quote(_CONTACT_MESSAGE)}'


def nav_categories(request):
    sub_subcategories = Category.objects.filter(
        aktivan=True, prikazi_u_meniju=True,
    ).order_by('redoslijed', 'naziv')

    subcategories = Category.objects.filter(
        aktivan=True, prikazi_u_meniju=True,
    ).order_by('redoslijed', 'naziv').prefetch_related(
        Prefetch('podkategorije', queryset=sub_subcategories),
    )

    categories = Category.objects.filter(
        roditelj__isnull=True, aktivan=True, prikazi_u_meniju=True,
    ).order_by('redoslijed', 'naziv').prefetch_related(
        Prefetch('podkategorije', queryset=subcategories),
    )

    cart = Cart(request)
    active_popup = None
    for popup in Popup.objects.filter(aktivan=True).select_related(
        'akcija_artikal', 'akcija_artikal__brend',
    ).order_by('redoslijed', '-id'):
        if popup.prikazi_korisniku(request.user):
            active_popup = popup
            break

    site_settings = SiteSettings.load()
    contact_phone = (site_settings.kontakt_telefon or settings.STORE_PHONE or '').strip()

    return {
        'site_url': settings.SITE_URL,
        'nav_categories': categories,
        'site_settings': site_settings,
        'cart_count': len(cart),
        'active_popup': active_popup,
        'active_upsell_offer': get_active_upsell_offer(request),
        'search_query': request.GET.get('q', '').strip(),
        'contact_phone': contact_phone,
        'contact_whatsapp_url': _whatsapp_contact_url(contact_phone),
    }