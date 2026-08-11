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


def _build_nav_categories():
    """Meniji kategorija — skupo, pa se cache-ira."""
    from django.core.cache import cache

    cache_key = 'nav_categories_tree_v1'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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

    categories = list(
        filter_categories_with_products(
            Category.objects.filter(
                roditelj__isnull=True, aktivan=True, prikazi_u_meniju=True,
            ),
            populated_category_ids,
        ).order_by('redoslijed', 'naziv').prefetch_related(
            Prefetch('podkategorije', queryset=subcategories),
        )
    )
    # Evaluiraj queryset sada (dok je DB topao) pa stavi u cache
    for cat in categories:
        list(cat.podkategorije.all())
        for sub in cat.podkategorije.all():
            list(sub.podkategorije.all())

    cache.set(cache_key, categories, 300)
    return categories


def _is_light_request_path(path: str) -> bool:
    """Putanje bez menija/popupova/marketing contexta (brži TTFB)."""
    path = path or ''
    if path.startswith(('/api/', '/uzivo/', '/static/', '/media/', '/admin/')):
        return True
    if path.startswith(('/sitemap', '/robots.txt', '/healthz', '/favicon', '/feeds/')):
        return True
    return False


def _cached_popup_akcije():
    """Lista aktivnih popup akcija — cache 30s (filter po useru ostaje u Pythonu)."""
    from django.core.cache import cache

    cache_key = 'active_popup_akcije_v2'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    rows = list(
        Akcija.objects.filter(
            aktivan=True,
            tip__in=Akcija.POPUP_TIPS,
        ).select_related(
            'artikal', 'artikal__brend', 'kategorija',
        ).prefetch_related(
            'bundle_artikli',
            'bundle_lines__product',
            'qty_tiers',
        ).order_by('redoslijed', '-id')[:20]
    )
    cache.set(cache_key, rows, 60)
    return rows


def nav_categories(request):
    # Jeftin path za API / sitemap / health — manje posla na svakom requestu
    path = getattr(request, 'path', '') or ''
    is_light = _is_light_request_path(path)

    if is_light:
        try:
            site_settings = SiteSettings.load()
        except Exception:
            site_settings = SiteSettings()
        return {
            'site_url': settings.SITE_URL,
            'nav_categories': [],
            'site_settings': site_settings,
            'cart_count': 0,
            'active_akcija': None,
            'active_popup': None,
            'popup_queue': [],
            'active_upsell_offer': None,
            'cart_recovery_alert': None,
            'cart_abandon_exit': None,
            'cart_exit_popup': None,
            'live_visitor_offer': None,
            'online_gift': None,
            'online_gift_reward_label': None,
            'search_query': '',
            'contact_phone': '',
            'contact_phone_digits': '',
            'contact_whatsapp_url': '',
            'contact_viber_url': '',
            'contact_messenger_url': '',
            'theme_ui': {},
            'social_proof': None,
            'dwell_flash_by_id': {},
            'dwell_catalog_by_id': {},
            'dwell_ui': {
                'active': False,
                'tag_text': 'Ograničena ponuda',
                'timer_label': 'Ističe za',
                'catalog_label': '',
                'flash_seconds': 120,
                'sale_pulse': True,
                'css_vars': '',
            },
            'staff_edit_mode': False,
            'organization_json_ld': '',
            'website_json_ld': '',
        }

    categories = _build_nav_categories()

    cart = Cart(request)

    popup_queue = []
    active_akcija = None
    for akcija in _cached_popup_akcije():
        if akcija.je_popup() and akcija.prikazi_korisniku(request.user, request=request):
            popup_queue.append(akcija)

    popup_queue.sort(
        key=lambda a: (a.popup_delay_seconds or 0, a.redoslijed, -a.id),
    )
    active_akcija = popup_queue[0] if popup_queue else None

    try:
        site_settings = SiteSettings.load()
    except Exception:
        site_settings = SiteSettings()

    contact_phone = (getattr(site_settings, 'kontakt_telefon', None) or settings.STORE_PHONE or '').strip()
    messenger_page = (
        getattr(site_settings, 'kontakt_messenger', None)
        or getattr(settings, 'MESSENGER_PAGE', '')
        or 'opremazaribolov.ba'
    ).strip()

    try:
        theme_ui = site_settings.get_theme_ui()
    except Exception:
        theme_ui = {
            'css_vars': '',
            'kontakt_prikazi_whatsapp': True,
            'kontakt_prikazi_viber': True,
            'kontakt_prikazi_messenger': True,
        }

    contact_whatsapp_url = (
        _whatsapp_contact_url(contact_phone)
        if theme_ui.get('kontakt_prikazi_whatsapp', True)
        else ''
    )
    contact_viber_url = (
        _viber_contact_url(contact_phone)
        if theme_ui.get('kontakt_prikazi_viber', True)
        else ''
    )
    contact_messenger_url = (
        _messenger_contact_url(messenger_page)
        if theme_ui.get('kontakt_prikazi_messenger', True)
        else ''
    )

    cart_abandon_exit = None
    cart_exit_popup = None
    dwell_flash_by_id = {}
    dwell_catalog_by_id = {}
    dwell_ui = {
        'active': False,
        'tag_text': 'Ograničena ponuda',
        'timer_label': 'Ističe za',
        'catalog_label': '',
        'flash_seconds': 120,
        'sale_pulse': True,
        'css_vars': '',
    }
    active_upsell = None
    cart_recovery = None
    live_offer = None
    online_gift = None
    gift_label = None
    social_proof = None

    cart_abandon_exit = get_cart_abandon_exit_context(request, cart)
    cart_exit_popup = (
        None if cart_abandon_exit else get_cart_exit_popup_context(request, cart)
    )
    try:
        from .live_visitor_offer import (
            get_all_active_dwell_flashes,
            get_dwell_catalog_map,
            get_dwell_ui,
        )
        dwell_flash_by_id = get_all_active_dwell_flashes(request)
        dwell_catalog_by_id = get_dwell_catalog_map(request)
        dwell_ui = get_dwell_ui()
    except Exception:
        pass
    active_upsell = get_active_upsell_offer(request)
    cart_recovery = get_active_cart_recovery_alert(request, cart)
    live_offer = build_live_visitor_offer_context(request)
    online_gift = build_online_gift_context(request)
    gift_label = active_reward_label(request)
    social_proof = build_social_proof_context(request)

    staff_edit_mode = False

    # SEO JSON-LD — cache (isti za sve HTML stranice)
    organization_json_ld = ''
    website_json_ld = ''
    try:
        from django.core.cache import cache
        from .utils.seo import json_ld, organization_json_ld as _org_ld, website_json_ld as _web_ld
        org_key = 'seo_org_json_ld_v1'
        web_key = 'seo_web_json_ld_v1'
        organization_json_ld = cache.get(org_key)
        website_json_ld = cache.get(web_key)
        if organization_json_ld is None or website_json_ld is None:
            organization_json_ld = json_ld(_org_ld(site_settings))
            website_json_ld = json_ld(_web_ld(site_settings))
            cache.set(org_key, organization_json_ld, 300)
            cache.set(web_key, website_json_ld, 300)
    except Exception:
        pass

    return {
        'site_url': settings.SITE_URL,
        'nav_categories': categories,
        'site_settings': site_settings,
        'cart_count': len(cart),
        'active_akcija': active_akcija,
        'active_popup': active_akcija,
        'popup_queue': popup_queue,
        'active_upsell_offer': active_upsell,
        'cart_recovery_alert': cart_recovery,
        'cart_abandon_exit': cart_abandon_exit,
        'cart_exit_popup': cart_exit_popup,
        'live_visitor_offer': live_offer,
        'online_gift': online_gift,
        'online_gift_reward_label': gift_label,
        'search_query': request.GET.get('q', '').strip(),
        'contact_phone': contact_phone,
        'contact_phone_digits': _phone_digits(contact_phone),
        'contact_whatsapp_url': contact_whatsapp_url,
        'contact_viber_url': contact_viber_url,
        'contact_messenger_url': contact_messenger_url,
        'theme_ui': theme_ui,
        'social_proof': social_proof,
        'dwell_flash_by_id': dwell_flash_by_id,
        'dwell_catalog_by_id': dwell_catalog_by_id,
        'dwell_ui': dwell_ui,
        'staff_edit_mode': staff_edit_mode,
        'organization_json_ld': organization_json_ld,
        'website_json_ld': website_json_ld,
    }
