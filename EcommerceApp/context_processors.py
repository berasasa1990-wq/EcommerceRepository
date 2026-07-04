import re
from urllib.parse import quote

from django.conf import settings
from django.db.models import Prefetch

from .cart import Cart
from .category_visibility import filter_categories_with_products, get_category_ids_with_products
from .models import Akcija, Category, SiteSettings
from .upsell import get_active_upsell_offer

_CONTACT_MESSAGE = 'Zdravo, imam pitanje sa opremazaribolov.ba'


def _phone_digits(phone):
    digits = re.sub(r'\D', '', phone or '')
    if digits.startswith('00'):
        digits = digits[2:]
    return digits


def _whatsapp_contact_url(phone):
    digits = _phone_digits(phone)
    if not digits:
        return ''
    return f'https://wa.me/{digits}?text={quote(_CONTACT_MESSAGE)}'


def _viber_contact_url(phone):
    digits = _phone_digits(phone)
    if not digits:
        return ''
    return f'viber://chat?number=%2B{digits}'


def _messenger_contact_url(page_slug):
    slug = (page_slug or '').strip().strip('/')
    if not slug:
        return ''
    if 'facebook.com/' in slug:
        slug = slug.rsplit('facebook.com/', 1)[-1].split('/')[0].split('?')[0]
    return f'https://m.me/{slug}'


def meta_pixel(request):
    return {
        'meta_pixel_id': getattr(settings, 'META_PIXEL_ID', ''),
        'meta_page_view_event_id': getattr(request, 'meta_page_view_event_id', None),
    }


def nav_categories(request):
    populated_category_ids = get_category_ids_with_products()

    sub_subcategories = filter_categories_with_products(
        Category.objects.filter(aktivan=True, prikazi_u_meniju=True),
        populated_category_ids,
    ).order_by('redoslijed', 'naziv')

    subcategories = filter_categories_with_products(
        Category.objects.filter(aktivan=True, prikazi_u_meniju=True),
        populated_category_ids,
    ).order_by('redoslijed', 'naziv').prefetch_related(
        Prefetch('podkategorije', queryset=sub_subcategories),
    )

    categories = filter_categories_with_products(
        Category.objects.filter(
            roditelj__isnull=True, aktivan=True, prikazi_u_meniju=True,
        ),
        populated_category_ids,
    ).order_by('redoslijed', 'naziv').prefetch_related(
        Prefetch('podkategorije', queryset=subcategories),
    )

    cart = Cart(request)
    active_akcija = None
    for akcija in Akcija.objects.filter(aktivan=True).select_related(
        'artikal', 'artikal__brend',
    ).order_by('redoslijed', '-id'):
        if akcija.je_popup() and akcija.prikazi_korisniku(request.user):
            active_akcija = akcija
            break

    site_settings = SiteSettings.load()
    contact_phone = (site_settings.kontakt_telefon or settings.STORE_PHONE or '').strip()
    messenger_page = (
        site_settings.kontakt_messenger
        or getattr(settings, 'MESSENGER_PAGE', '')
        or 'opremazaribolov.ba'
    ).strip()

    return {
        'site_url': settings.SITE_URL,
        'nav_categories': categories,
        'site_settings': site_settings,
        'cart_count': len(cart),
        'active_akcija': active_akcija,
        'active_popup': active_akcija,
        'active_upsell_offer': get_active_upsell_offer(request),
        'search_query': request.GET.get('q', '').strip(),
        'contact_phone': contact_phone,
        'contact_whatsapp_url': _whatsapp_contact_url(contact_phone),
        'contact_viber_url': _viber_contact_url(contact_phone),
        'contact_messenger_url': _messenger_contact_url(messenger_page),
    }