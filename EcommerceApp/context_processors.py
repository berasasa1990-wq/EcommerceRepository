import re
from urllib.parse import quote

from django.conf import settings
from django.db.models import Prefetch

from .cart import Cart
from .cart_exit_popup import get_cart_abandon_exit_context, get_cart_exit_popup_context
from .cart_recovery import get_active_cart_recovery_alert
from .social_proof import build_social_proof_context
from .live_visitor_offer import build_live_visitor_offer_context
from .category_visibility import filter_categories_with_products, get_category_ids_with_products
from .models import Akcija, Category, SiteSettings
from .online_gift import active_reward_label, build_online_gift_context
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
    popup_queue = []
    for akcija in Akcija.objects.filter(
        aktivan=True,
        tip__in=Akcija.ACTIVE_TIPS,
    ).select_related(
        'artikal', 'artikal__brend', 'kategorija',
    ).prefetch_related(
        'bundle_artikli',
        'bundle_lines__product',
        'qty_tiers',
    ).order_by('redoslijed', '-id'):
        if akcija.je_popup() and akcija.prikazi_korisniku(request.user, request=request):
            popup_queue.append(akcija)

    popup_queue.sort(
        key=lambda a: (a.popup_delay_seconds or 0, a.redoslijed, -a.id),
    )
    active_akcija = popup_queue[0] if popup_queue else None

    site_settings = SiteSettings.load()
    contact_phone = (site_settings.kontakt_telefon or settings.STORE_PHONE or '').strip()
    messenger_page = (
        site_settings.kontakt_messenger
        or getattr(settings, 'MESSENGER_PAGE', '')
        or 'opremazaribolov.ba'
    ).strip()

    # Exit s korpom: podsjetnik da završi narudžbu (prioritet nad product deal popupa)
    cart_abandon_exit = get_cart_abandon_exit_context(request, cart)
    cart_exit_popup = (
        None if cart_abandon_exit else get_cart_exit_popup_context(request, cart)
    )

    # AI dwell flash — snizene cijene u sesiji (početna / kartice)
    try:
        from .live_visitor_offer import get_all_active_dwell_flashes
        dwell_flash_by_id = get_all_active_dwell_flashes(request)
    except Exception:
        dwell_flash_by_id = {}

    return {
        'site_url': settings.SITE_URL,
        'nav_categories': categories,
        'site_settings': site_settings,
        'cart_count': len(cart),
        'active_akcija': active_akcija,
        'active_popup': active_akcija,
        'popup_queue': popup_queue,
        'active_upsell_offer': get_active_upsell_offer(request),
        'cart_recovery_alert': get_active_cart_recovery_alert(request, cart),
        'cart_abandon_exit': cart_abandon_exit,
        'cart_exit_popup': cart_exit_popup,
        'live_visitor_offer': build_live_visitor_offer_context(request),
        'online_gift': build_online_gift_context(request),
        'online_gift_reward_label': active_reward_label(request),
        'search_query': request.GET.get('q', '').strip(),
        'contact_phone': contact_phone,
        'contact_whatsapp_url': _whatsapp_contact_url(contact_phone),
        'contact_viber_url': _viber_contact_url(contact_phone),
        'contact_messenger_url': _messenger_contact_url(messenger_page),
        'social_proof': build_social_proof_context(request),
        'dwell_flash_by_id': dwell_flash_by_id,
    }