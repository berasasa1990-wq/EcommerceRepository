import logging
import random
import re
import requests
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlparse

from django.conf import settings
from .models import SiteSettings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.db.models import Count, Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape, mark_safe, strip_tags
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from .cart import Cart
from .loyalty import (
    azuriraj_loyalty_nakon_narudzbe,
    kreiraj_loyalty_karticu,
    loyalty_kontekst,
    osiguraj_loyalty_karticu,
    validiraj_kupon,
)
from .pricing import izracunaj_sazetak, sazetak_iz_narudzbe
from .emails import EmailNotConfiguredError, get_order_email_context, send_order_emails
from .render_sync import sync_korisnik, sync_narudzba
from .utils.images import image_field_dimensions

logger = logging.getLogger(__name__)
from .forms import CheckoutForm, CouponForm, LoginForm, ProfileForm, RegisterForm
from .models import (
    Banner,
    Brand,
    Category,
    HomeFeaturedProduct,
    HomeVlog,
    LoyaltyCard,
    Order,
    OrderItem,
    Product,
    ProductImage,
    ProductVariation,
    SiteSettings,
    UpsellOffer,
    UserProfile,
)


def _in_stock_variations_qs():
    return ProductVariation.objects.filter(
        na_stanju=True,
    ).order_by('redoslijed', 'id')


def _prefetch_product_cards(qs):
    return qs.select_related('kategorija', 'brend').annotate(
        variation_count=Count('varijacije'),
    ).prefetch_related(
        Prefetch('varijacije', queryset=_in_stock_variations_qs()),
    )


def _product_queryset():
    return _prefetch_product_cards(
        Product.objects.filter(aktivan=True, na_stanju=True),
    )


def _effective_product_price(product):
    variations = list(product.varijacije.all())
    if variations:
        return min(variation.prikazna_cijena for variation in variations)
    return product.prikazna_cijena


def _product_is_on_sale(product):
    variations = list(product.varijacije.all())
    if variations:
        return any(variation.na_akciji for variation in variations)
    return product.na_akciji


def _akcija_products_qs(products_qs):
    sale_ids = [
        product.pk
        for product in products_qs
        if _product_is_on_sale(product)
    ]
    if not sale_ids:
        return products_qs.none()
    return products_qs.filter(pk__in=sale_ids)


def _filter_size_scope_qs(filter_params, base_qs=None):
    qs = base_qs if base_qs is not None else _product_queryset()
    if filter_params.get('q'):
        qs = _apply_search_filter(qs, filter_params['q'])
    if filter_params.get('akcija'):
        qs = _akcija_products_qs(qs)
    return qs


def _filter_reset_url(filter_action, filter_params):
    preserved = {}
    if filter_params.get('akcija'):
        preserved['akcija'] = filter_params['akcija']
    if filter_params.get('q'):
        preserved['q'] = filter_params['q']
    if filter_params.get('brend'):
        preserved['brend'] = filter_params['brend']
    query = urlencode(preserved)
    if query:
        return f'{filter_action}?{query}#product-showcase'
    return f'{filter_action}#product-showcase'


def _parse_decimal(value):
    value = (value or '').strip().replace(',', '.')
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


_SIZE_EXACT = re.compile(r'^#\d+(?:/\d+)?$', re.I)
_SIZE_HASH = re.compile(r'(#\d+(?:/\d+)?)', re.I)
_SIZE_PLAIN = re.compile(r'^\d+$')
_SIZE_CM = re.compile(r'(\d+(?:[.,]\d+)?)\s*cm\b', re.I)
_SIZE_MM = re.compile(r'(\d+(?:[.,]\d+)?)\s*mm\b', re.I)
_SIZE_GRAM = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:g|gr|gram|grama)\b', re.I)
_REEL_SIZES = frozenset({
    '1000', '1500', '2000', '2500', '3000', '4000', '4500', '5000', '5500',
    '6000', '6500', '7000', '8000', '10000', '12000',
})
_REEL_SIZE_PATTERN = re.compile(
    r'(?<!\d)(' + '|'.join(sorted(_REEL_SIZES, key=len, reverse=True)) + r')(?!\d)',
    re.I,
)


def _normalize_size_number(value):
    normalized = (value or '').strip().replace(',', '.')
    if '.' in normalized:
        normalized = normalized.rstrip('0').rstrip('.')
    return normalized


def _variation_size_label(naziv):
    """Vraća veličinu iz naziva (#broj, cm, mm, g ili veličina mašinice), ako postoji."""
    naziv = (naziv or '').strip()
    if not naziv:
        return None
    if _SIZE_EXACT.match(naziv):
        return naziv
    if _SIZE_PLAIN.match(naziv):
        if naziv in _REEL_SIZES:
            return naziv
        return f'#{naziv}'
    hash_match = _SIZE_HASH.search(naziv)
    if hash_match:
        return hash_match.group(1)
    cm_match = _SIZE_CM.search(naziv)
    if cm_match:
        return f'{_normalize_size_number(cm_match.group(1))} cm'
    gram_match = _SIZE_GRAM.search(naziv)
    if gram_match:
        return f'{_normalize_size_number(gram_match.group(1))} g'
    mm_match = _SIZE_MM.search(naziv)
    if mm_match:
        return f'{_normalize_size_number(mm_match.group(1))} mm'
    reel_match = _REEL_SIZE_PATTERN.search(naziv)
    if reel_match:
        return reel_match.group(1)
    return None


def _size_sort_key(label):
    label = label or ''
    hook_match = re.search(r'#(\d+)', label)
    if hook_match:
        return (0, int(hook_match.group(1)), label)
    unit_match = re.match(r'^(\d+(?:\.\d+)?)\s*(cm|mm|g)$', label, re.I)
    if unit_match:
        unit = unit_match.group(2).lower()
        unit_rank = {'cm': 1, 'mm': 2, 'g': 3}.get(unit, 9)
        return (unit_rank, float(unit_match.group(1)), label)
    if label.isdigit():
        return (3, int(label), label)
    return (9, 0, label)


_SIZE_FILTER_GROUPS = (
    ('duzina', 'Dužina', 'Prikaži sve dužine'),
    ('debljina', 'Debljina', 'Prikaži sve debljine'),
    ('gramaza', 'Gramaža', 'Prikaži sve gramaže'),
    ('velicina', 'Veličina', 'Prikaži sve veličine'),
)


def _size_filter_group_key(label):
    label = (label or '').strip()
    if re.match(r'^\d+(?:\.\d+)?\s*cm$', label, re.I):
        return 'duzina'
    if re.match(r'^\d+(?:\.\d+)?\s*mm$', label, re.I):
        return 'debljina'
    if re.match(r'^\d+(?:\.\d+)?\s*g$', label, re.I):
        return 'gramaza'
    if label.startswith('#') or label in _REEL_SIZES or label.isdigit():
        return 'velicina'
    return 'velicina'


def _available_sizes(products_qs):
    nazivi = ProductVariation.objects.filter(
        artikal__in=products_qs,
        na_stanju=True,
    ).values_list('naziv', flat=True)
    sizes = {_variation_size_label(naziv) for naziv in nazivi}

    for naziv in Product.objects.filter(
        pk__in=products_qs.values('pk'),
        na_stanju=True,
    ).annotate(
        variation_count=Count('varijacije'),
    ).filter(variation_count=0).values_list('naziv', flat=True):
        label = _variation_size_label(naziv)
        if label:
            sizes.add(label)

    sizes.discard(None)
    return sorted(sizes, key=_size_sort_key)


def _product_matches_size(product, size_label):
    if any(
        variation.na_stanju and _variation_size_label(variation.naziv) == size_label
        for variation in product.varijacije.all()
    ):
        return True
    if getattr(product, 'variation_count', 0) == 0:
        return product.na_stanju and _variation_size_label(product.naziv) == size_label
    return False


def _get_filter_params(request):
    return {
        'q': request.GET.get('q', '').strip(),
        'kategorija': request.GET.get('kategorija', '').strip(),
        'brend': request.GET.get('brend', '').strip(),
        'velicina': request.GET.get('velicina', '').strip(),
        'cijena_od': request.GET.get('cijena_od', '').strip(),
        'cijena_do': request.GET.get('cijena_do', '').strip(),
        'sort': request.GET.get('sort', '').strip(),
        'akcija': request.GET.get('akcija', '').strip(),
    }


def _filters_active(params):
    return any(params.values())


def _filter_categories():
    return Category.objects.filter(aktivan=True).select_related(
        'roditelj', 'roditelj__roditelj',
    ).order_by('redoslijed', 'naziv')


def _showcase_brands():
    return Brand.objects.filter(
        slika__isnull=False,
        artikli__aktivan=True,
        artikli__na_stanju=True,
    ).exclude(slika='').distinct().order_by('naziv')


def _apply_search_filter(products_qs, query):
    if not query:
        return products_qs
    return products_qs.filter(
        Q(naziv__icontains=query)
        | Q(sifra__icontains=query)
        | Q(tagovi__naziv__icontains=query)
        | Q(varijacije__sifra__icontains=query)
        | Q(kategorija__naziv__icontains=query)
        | Q(kategorija__roditelj__naziv__icontains=query),
    ).distinct()


SEARCH_SUGGEST_LIMIT = 6


def search_suggest(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': [], 'query': '', 'has_more': False})

    products_qs = _apply_search_filter(_product_queryset(), query)
    products = list(products_qs[:SEARCH_SUGGEST_LIMIT + 1])
    has_more = len(products) > SEARCH_SUGGEST_LIMIT
    products = products[:SEARCH_SUGGEST_LIMIT]
    results = []
    for product in products:
        price = _effective_product_price(product)
        results.append({
            'naziv': product.naziv,
            'url': product.get_absolute_url(),
            'image': product.prikazna_slika.url if product.prikazna_slika else '',
            'price': f'{price:.2f}',
            'on_sale': _product_is_on_sale(product),
        })

    return JsonResponse({'results': results, 'query': query, 'has_more': has_more})


def _apply_product_filters(products_qs, request, *, allowed_category_ids=None):
    params = _get_filter_params(request)
    products_qs = _apply_search_filter(products_qs, params['q'])
    products = list(products_qs)

    if allowed_category_ids is not None:
        allowed = set(allowed_category_ids)
        products = [product for product in products if product.kategorija_id in allowed]

    if params['kategorija']:
        category = Category.objects.filter(slug=params['kategorija'], aktivan=True).first()
        if category:
            category_ids = set(category.get_descendant_ids())
            if allowed_category_ids is not None:
                category_ids &= set(allowed_category_ids)
            products = [product for product in products if product.kategorija_id in category_ids]

    if params['brend']:
        brand = Brand.objects.filter(slug=params['brend']).first()
        if brand:
            products = [product for product in products if product.brend_id == brand.pk]

    price_min = _parse_decimal(params['cijena_od'])
    price_max = _parse_decimal(params['cijena_do'])
    if price_min is not None:
        products = [product for product in products if _effective_product_price(product) >= price_min]
    if price_max is not None:
        products = [product for product in products if _effective_product_price(product) <= price_max]

    if params['akcija']:
        products = [product for product in products if _product_is_on_sale(product)]

    if params['velicina']:
        size_label = params['velicina']
        products = [
            product for product in products
            if _product_matches_size(product, size_label)
        ]

    if params['sort'] == 'rastuca':
        products.sort(key=_effective_product_price)
    elif params['sort'] == 'opadajuca':
        products.sort(key=_effective_product_price, reverse=True)

    return products, params


HOME_PRODUCTS_PER_PAGE = 18
HOME_PRODUCT_ORDER_KEY = 'home_product_ids'
HOME_FILTER_KEY = 'home_filter_key'


def _catalog_query_string(filter_params, page=None, **overrides):
    params = {key: value for key, value in filter_params.items() if value}
    for key, value in overrides.items():
        if value:
            params[key] = value
        else:
            params.pop(key, None)
    if page and page > 1:
        params['page'] = page
    return urlencode(params)


def _build_filter_url(filter_action, filter_params, **overrides):
    query = _catalog_query_string(filter_params, **overrides)
    if query:
        return f'{filter_action}?{query}#product-showcase'
    return f'{filter_action}#product-showcase'


def _size_filter_groups(filter_action, filter_params, sizes):
    grouped = {key: [] for key, _, _ in _SIZE_FILTER_GROUPS}
    for size in sizes:
        group_key = _size_filter_group_key(size)
        grouped[group_key].append({
            'label': size,
            'url': _build_filter_url(filter_action, filter_params, velicina=size),
            'selected': filter_params.get('velicina') == size,
        })

    selected = filter_params.get('velicina', '')
    selected_group = _size_filter_group_key(selected) if selected else ''
    groups = []
    for key, title, clear_label in _SIZE_FILTER_GROUPS:
        options = grouped.get(key, [])
        if not options:
            continue
        groups.append({
            'label': title,
            'options': options,
            'clear_url': (
                _build_filter_url(filter_action, filter_params, velicina='')
                if selected_group == key else ''
            ),
            'clear_label': clear_label,
        })
    return groups


def _paginate_home_products(request, products, filter_params):
    page_number = request.GET.get('page', '1')
    filters_active = _filters_active(filter_params)
    filter_signature = _catalog_query_string(filter_params)

    if filters_active:
        if request.session.get(HOME_FILTER_KEY) != filter_signature:
            request.session.pop(HOME_PRODUCT_ORDER_KEY, None)
            request.session[HOME_FILTER_KEY] = filter_signature
            request.session.modified = True
    else:
        request.session.pop(HOME_FILTER_KEY, None)
        fresh_visit = 'page' not in request.GET
        if fresh_visit:
            random.shuffle(products)
            request.session[HOME_PRODUCT_ORDER_KEY] = [product.pk for product in products]
            request.session.modified = True
        else:
            stored_ids = request.session.get(HOME_PRODUCT_ORDER_KEY, [])
            if stored_ids:
                by_id = {product.pk: product for product in products}
                ordered = [by_id[pk] for pk in stored_ids if pk in by_id]
                seen = {product.pk for product in ordered}
                for product in products:
                    if product.pk not in seen:
                        ordered.append(product)
                products = ordered

    paginator = Paginator(products, HOME_PRODUCTS_PER_PAGE)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages or 1)
    return page_obj


def _base_context():
    return {}


def _banner_secondary_href(link):
    if not link:
        return None
    if link.startswith(('http://', 'https://', '/')):
        return link
    return f'/{link.strip("/")}/'


def _banner_actions(banner):
    actions = []
    if banner.tekst_dugmeta:
        actions.append({
            'label': banner.tekst_dugmeta,
            'url': banner.get_link_href() or '#',
            'primary': True,
        })
    if banner.sekundarno_dugme:
        actions.append({
            'label': banner.sekundarno_dugme,
            'url': _banner_secondary_href(banner.sekundarni_link) or '#',
            'primary': False,
        })
    return actions


def _banner_media_meta(banner, *, tip='hero', default=(1920, 560)):
    from .utils.images import banner_image_responsive_meta

    image_meta = {
        'src': '',
        'srcset': '',
        'width': default[0],
        'height': default[1],
    }
    if banner.slika:
        image_meta = banner_image_responsive_meta(
            banner.slika,
            tip=tip,
            default=default,
        )
    video_url = banner.video.url if banner.video else None
    return {
        'image': image_meta['src'],
        'image_srcset': image_meta['srcset'],
        'image_width': image_meta['width'],
        'image_height': image_meta['height'],
        'video': video_url,
        'has_video': bool(video_url),
    }


def _banner_to_hero_slide(banner):
    media = _banner_media_meta(banner, tip='hero', default=(1920, 560))
    return {
        'title': banner.naslov,
        'subtitle': banner.podnaslov,
        'url': banner.get_link_href(),
        'actions': _banner_actions(banner),
        **media,
    }


def _banner_to_card(banner):
    default_dims = (360, 360) if banner.tip == Banner.BannerType.GRID else (1200, 800)
    media = _banner_media_meta(banner, tip=banner.tip, default=default_dims)
    return {
        'title': banner.naslov,
        'subtitle': banner.podnaslov,
        'url': banner.get_link_href(),
        'actions': _banner_actions(banner),
        'wide': banner.siroka_kartica,
        **media,
    }


def _banners_with_media(qs):
    from django.db.models import Q
    return qs.filter(
        Q(slika__isnull=False) & ~Q(slika='')
        | Q(video__isnull=False) & ~Q(video=''),
    )


HOME_SECTION_PRODUCT_LIMIT = 10
HOME_SECTION_PRODUCT_VISIBLE = 6
HOME_SECTION_PRODUCT_VISIBLE_MOBILE = 2
HOME_VLOG_LIMIT = 3


def _home_latest_products():
    return list(
        _product_queryset().order_by('-kreiran')[:HOME_SECTION_PRODUCT_LIMIT],
    )


def _home_featured_products():
    entries = HomeFeaturedProduct.objects.filter(
        aktivan=True,
        artikal__aktivan=True,
        artikal__na_stanju=True,
    ).select_related(
        'artikal', 'artikal__kategorija', 'artikal__brend',
    ).prefetch_related(
        Prefetch('artikal__varijacije', queryset=_in_stock_variations_qs()),
    )[:HOME_SECTION_PRODUCT_LIMIT]
    return [entry.artikal for entry in entries]


def _related_category_products(product, limit=HOME_SECTION_PRODUCT_LIMIT):
    if not product.kategorija_id:
        return []
    return list(
        _product_queryset()
        .filter(kategorija_id=product.kategorija_id)
        .exclude(pk=product.pk)
        .order_by('-kreiran')[:limit],
    )


def _vlog_cards(limit=None):
    try:
        entries_qs = HomeVlog.objects.filter(
            aktivan=True,
        ).exclude(
            slika='',
        ).exclude(
            slug='',
        ).order_by('redoslijed', '-id')
        if limit is not None:
            entries = list(entries_qs[:limit])
        else:
            entries = list(entries_qs)
    except DatabaseError:
        logger.exception(
            'HomeVlog tabela nije dostupna — pokreni: python manage.py migrate',
        )
        return []

    vlogs = []
    for vlog in entries:
        if not vlog.slug:
            continue
        from .utils.images import vlog_image_responsive_meta

        image_meta = vlog_image_responsive_meta(vlog.slika, default=(360, 360))
        vlogs.append({
            'id': vlog.pk,
            'slug': vlog.slug,
            'naslov': vlog.naslov,
            'slika_url': image_meta['src'],
            'slika_srcset': image_meta['srcset'],
            'image_width': image_meta['width'],
            'image_height': image_meta['height'],
        })
    return vlogs


def _home_vlogs():
    return _vlog_cards(HOME_VLOG_LIMIT)


def _vlog_seo_description(sadrzaj, max_len=160):
    text = strip_tags(sadrzaj).strip()
    if len(text) <= max_len:
        return text
    trimmed = text[:max_len - 1]
    if ' ' in trimmed:
        trimmed = trimmed.rsplit(' ', 1)[0]
    return f'{trimmed}…'


def home(request):
    hero_banners = _banners_with_media(Banner.objects.filter(
        tip=Banner.BannerType.HERO, aktivan=True,
    ).order_by('redoslijed', '-id'))
    grid_banners = _banners_with_media(Banner.objects.filter(
        tip=Banner.BannerType.GRID, aktivan=True,
    ).order_by('redoslijed', '-id'))[:8]
    featured_banners = _banners_with_media(Banner.objects.filter(
        tip=Banner.BannerType.FEATURED, aktivan=True,
    ).order_by('redoslijed', '-id'))
    spotlight_banner = _banners_with_media(Banner.objects.filter(
        tip=Banner.BannerType.SPOTLIGHT, aktivan=True,
    ).order_by('redoslijed', '-id')).first()

    filter_params = _get_filter_params(request)
    filters_active = _filters_active(filter_params)

    latest_products = []
    featured_products = []
    home_vlogs = []
    page_obj = None
    search_products = []
    catalog_title = None
    catalog_subtitle = None
    filter_size_groups = []
    home_url = reverse('home')

    if filters_active:
        products, filter_params = _apply_product_filters(_product_queryset(), request)
        scope_qs = _filter_size_scope_qs(filter_params)
        filter_sizes = _available_sizes(scope_qs)
        filter_size_groups = _size_filter_groups(home_url, filter_params, filter_sizes)
        page_obj = _paginate_home_products(request, products, filter_params)
        search_products = page_obj.object_list
        result_count = page_obj.paginator.count
        if filter_params.get('q'):
            catalog_title = 'Rezultati pretrage'
            if result_count:
                catalog_subtitle = (
                    f'Pronađeno {result_count} artikala za „{filter_params["q"]}".'
                )
            else:
                catalog_subtitle = f'Nema artikala za „{filter_params["q"]}".'
        elif filter_params.get('akcija'):
            catalog_title = 'Akcija'
            if result_count:
                catalog_subtitle = f'{result_count} artikala na sniženoj cijeni.'
            else:
                catalog_subtitle = 'Trenutno nema artikala na akciji.'
        elif filter_params.get('brend'):
            brand = Brand.objects.filter(slug=filter_params['brend']).first()
            if brand:
                catalog_title = brand.naziv
                if result_count:
                    catalog_subtitle = f'{result_count} artikala brenda {brand.naziv}.'
                else:
                    catalog_subtitle = 'Nema artikala za odabrani brend.'
        else:
            catalog_title = 'Rezultati'
            if result_count:
                catalog_subtitle = f'{result_count} artikala.'
    else:
        latest_products = _home_latest_products()
        featured_products = _home_featured_products()
        home_vlogs = _home_vlogs()

    first_hero = hero_banners.first()
    first_grid_banner = grid_banners.first()
    has_hero_slides = bool(not filters_active and hero_banners.exists())
    lcp_image_url = None
    lcp_image_srcset = None
    lcp_image_sizes = None
    eager_first_novo_image = False
    if not filters_active:
        if first_hero and first_hero.slika:
            from .utils.images import banner_image_responsive_meta

            hero_lcp = banner_image_responsive_meta(
                first_hero.slika,
                tip='hero',
                default=(1920, 560),
            )
            lcp_image_url = request.build_absolute_uri(
                hero_lcp.get('preload_src') or hero_lcp['src'],
            )
            lcp_image_srcset = hero_lcp.get('srcset') or None
            lcp_image_sizes = '100vw'
        elif first_grid_banner and first_grid_banner.slika:
            from .utils.images import banner_image_responsive_meta

            grid_lcp = banner_image_responsive_meta(
                first_grid_banner.slika,
                tip='grid',
                default=(360, 360),
            )
            lcp_image_url = request.build_absolute_uri(
                grid_lcp.get('preload_src') or grid_lcp['src'],
            )
            lcp_image_srcset = grid_lcp.get('srcset') or None
            lcp_image_sizes = '(max-width: 768px) 50vw, 360px'
        elif latest_products:
            first_product = latest_products[0]
            if first_product.prikazna_slika:
                product_lcp = first_product.prikazna_slika_responsive
                if product_lcp:
                    lcp_image_url = request.build_absolute_uri(
                        product_lcp.get('preload_src') or product_lcp['src'],
                    )
                    lcp_image_srcset = product_lcp.get('srcset') or None
                    lcp_image_sizes = '(max-width: 768px) 50vw, 16vw'
                else:
                    lcp_image_url = request.build_absolute_uri(
                        first_product.prikazna_slika.url,
                    )
                eager_first_novo_image = True

    spotlight = None
    if spotlight_banner:
        spotlight_media = _banner_media_meta(
            spotlight_banner,
            tip='spotlight',
            default=(1200, 800),
        )
        spotlight = {
            'title': spotlight_banner.naslov,
            'description': spotlight_banner.podnaslov,
            'cta': spotlight_banner.tekst_dugmeta,
            'url': spotlight_banner.get_link_href(),
            **spotlight_media,
        }

    context = {
        **_base_context(),
        'lcp_image_url': lcp_image_url,
        'lcp_image_srcset': lcp_image_srcset,
        'lcp_image_sizes': lcp_image_sizes,
        'has_hero_slides': has_hero_slides,
        'eager_first_novo_image': eager_first_novo_image,
        'hero_slides': [_banner_to_hero_slide(b) for b in hero_banners],
        'grid_banners': [_banner_to_card(b) for b in grid_banners],
        'featured_cards': [_banner_to_card(b) for b in featured_banners],
        'spotlight': spotlight,
        'latest_products': latest_products,
        'featured_products': featured_products,
        'home_vlogs': home_vlogs,
        'showcase_brands': _showcase_brands() if not filters_active else [],
        'search_products': search_products,
        'page_obj': page_obj,
        'filters_active': filters_active,
        'filter_params': filter_params,
        'filter_categories': _filter_categories() if filters_active else [],
        'filter_size_groups': filter_size_groups,
        'filter_action': home_url,
        'filter_reset_url': (
            _filter_reset_url(home_url, filter_params) if filters_active else ''
        ),
        'catalog_title': catalog_title,
        'catalog_subtitle': catalog_subtitle,
        'catalog_query': _catalog_query_string(filter_params) if filters_active else '',
        'elided_page_range': (
            page_obj.paginator.get_elided_page_range(page_obj.number) if page_obj else []
        ),
        'selected_brand': Brand.objects.filter(slug=filter_params['brend']).first() if filter_params.get('brend') else None,
        'home_section_product_visible': HOME_SECTION_PRODUCT_VISIBLE,
        'home_section_product_visible_mobile': HOME_SECTION_PRODUCT_VISIBLE_MOBILE,
        'canonical_url': settings.SITE_URL.rstrip('/') + '/',
    }
    return render(request, 'home.html', context)


def vlog_detail(request, slug):
    try:
        vlog = get_object_or_404(HomeVlog, slug=slug, aktivan=True)
    except DatabaseError:
        logger.exception(
            'HomeVlog tabela nije dostupna — pokreni: python manage.py migrate',
        )
        raise Http404 from None
    from .utils.images import vlog_image_responsive_meta

    vlog_image = vlog_image_responsive_meta(vlog.slika, default=(360, 360))
    other_vlogs = []
    for other in HomeVlog.objects.filter(aktivan=True).exclude(slika='').exclude(pk=vlog.pk).order_by(
        'redoslijed', '-id',
    )[:3]:
        image_meta = vlog_image_responsive_meta(other.slika, default=(280, 280))
        other_vlogs.append({
            'slug': other.slug,
            'naslov': other.naslov,
            'slika_url': image_meta['src'],
            'slika_srcset': image_meta['srcset'],
            'image_width': image_meta['width'],
            'image_height': image_meta['height'],
        })

    lcp_image_url = request.build_absolute_uri(vlog_image['src'])
    seo_description = _vlog_seo_description(vlog.sadrzaj)

    context = {
        **_base_context(),
        'vlog': vlog,
        'other_vlogs': other_vlogs,
        'lcp_image_url': lcp_image_url,
        'vlog_image': vlog_image,
        'image_width': vlog_image['width'],
        'image_height': vlog_image['height'],
        'seo_title': f'{vlog.naslov} | Vlog — opremazaribolov.ba',
        'seo_description': seo_description,
        'canonical_url': settings.SITE_URL.rstrip('/') + vlog.get_absolute_url(),
        'og_image': request.build_absolute_uri(vlog_image['src']),
    }
    return render(request, 'vlog_detail.html', context)


def about_us(request):
    context = {
        **_base_context(),
        'seo_title': 'O nama — opremazaribolov.ba',
        'seo_description': (
            'Saznajte više o opremazaribolov.ba — dugogodišnje iskustvo u ribolovu '
            'i opremi, sada u online prodaji za ribare u Bosni i Hercegovini.'
        ),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('about_us'),
    }
    return render(request, 'pages/about.html', context)


def payment_methods(request):
    context = {
        **_base_context(),
        'seo_title': 'Način plaćanja — opremazaribolov.ba',
        'seo_description': (
            'Plaćanje prilikom preuzimanja, dostava brzom poštom u roku 48h i sigurno slanje pošiljki.'
        ),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('payment_methods'),
    }
    return render(request, 'pages/payment.html', context)


def vlog_list(request):
    context = {
        **_base_context(),
        'vlogs': _vlog_cards(),
        'seo_title': 'Blog — opremazaribolov.ba',
        'seo_description': (
            'Blog i vlog opremazaribolov.ba — savjeti, priče i novosti iz svijeta ribolova.'
        ),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('vlog_list'),
    }
    return render(request, 'vlog_list.html', context)


def category_detail(request, slug):
    category = get_object_or_404(
        Category.objects.select_related('roditelj').prefetch_related('podkategorije__podkategorije'),
        slug=slug, aktivan=True,
    )

    # Ako ima direktnih podkategorija i nije zatraženo "sve" (all=1),
    # prikaži lijepu stranicu sa podkategorijama (umjesto proizvoda)
    direct_subs = list(category.podkategorije.filter(aktivan=True).order_by('redoslijed', 'naziv'))
    show_all = request.GET.get('all') == '1'

    if direct_subs and not show_all:
        context = {
            **_base_context(),
            'category': category,
            'subcategories': direct_subs,
            'seo_title': category.meta_title or f"{category.naziv} | Oprema za ribolov",
            'seo_description': category.meta_description or f"Izaberite podkategoriju unutar {category.naziv}.",
            'canonical_url': settings.SITE_URL.rstrip('/') + category.get_absolute_url(),
        }
        return render(request, 'category_subcategories.html', context)

    # Normalan prikaz proizvoda (ili "Sve u kategoriji")
    category_ids = category.get_descendant_ids()
    products_qs = _product_queryset().filter(kategorija_id__in=category_ids)
    filter_sizes = _available_sizes(products_qs)
    category_url = reverse('category', args=[category.slug])
    products, filter_params = _apply_product_filters(
        products_qs,
        request,
        allowed_category_ids=category_ids,
    )

    context = {
        **_base_context(),
        'category': category,
        'products': products,
        'filter_categories': _filter_categories(),
        'filter_params': filter_params,
        'filter_size_groups': _size_filter_groups(category_url, filter_params, filter_sizes),
        'filter_reset_url': _filter_reset_url(category_url, filter_params),
        # SEO
        'seo_title': category.meta_title or f"{category.naziv} | Oprema za ribolov",
        'seo_description': category.meta_description or f"{category.naziv} — kvalitetna oprema za ribolov po povoljnim cijenama. Brza dostava širom Bosne i Hercegovine.",
        'canonical_url': settings.SITE_URL.rstrip('/') + category.get_absolute_url(),
    }
    return render(request, 'category.html', context)


def _product_back_url(request, product):
    referer = request.META.get('HTTP_REFERER', '')
    current_url = request.build_absolute_uri()
    if referer:
        ref = urlparse(referer)
        cur = urlparse(current_url)
        if ref.netloc == cur.netloc and referer.rstrip('/') != current_url.rstrip('/'):
            if product.get_absolute_url() not in ref.path:
                return referer
    if product.kategorija_id:
        return request.build_absolute_uri(product.kategorija.get_absolute_url())
    return request.build_absolute_uri(reverse('home'))


def product_detail(request, slug):
    # Allow sold-out products (na_stanju=False) to be shown on product page
    # but only active ones. We prefetch ALL variations (not just in-stock) so we
    # can display "Rasprodato" for out-of-stock variations.
    product = get_object_or_404(
        Product.objects.filter(aktivan=True)
        .select_related('kategorija', 'brend')
        .prefetch_related(
            Prefetch('varijacije', queryset=ProductVariation.objects.order_by('redoslijed', 'id')),
            Prefetch('dodatne_slike', queryset=ProductImage.objects.order_by('redoslijed', 'id')),
        ),
        slug=slug,
    )
    lcp_image_url = None
    product_image_width, product_image_height = 800, 800
    if product.prikazna_slika:
        product_image_width, product_image_height = image_field_dimensions(
            product.prikazna_slika, default=(800, 800),
        )
        lcp_image_url = request.build_absolute_uri(product.prikazna_slika.url)

    related_products = _related_category_products(product)
    site_settings = SiteSettings.load()
    kategorija_naziv = product.kategorija.naziv if product.kategorija else ''

    context = {
        **_base_context(),
        'product': product,
        'ima_varijacije': product.varijacije.count() > 0,
        'related_products': related_products,
        'povezani_podnaslov': site_settings.format_povezani_podnaslov(kategorija_naziv),
        'lcp_image_url': lcp_image_url,
        'product_image_width': product_image_width,
        'product_image_height': product_image_height,
        # SEO
        'seo_title': product.seo_title,
        'seo_description': product.seo_description,
        'canonical_url': settings.SITE_URL.rstrip('/') + product.get_absolute_url(),
        'og_image': (
            request.build_absolute_uri(product.prikazna_slika.url)
            if product.prikazna_slika else None
        ),
        'product_back_url': _product_back_url(request, product),
    }

    # X+1 deal promo for product detail (pulsating red box)
    from .upsell import get_deal_promo_data
    deal_promo = get_deal_promo_data(product)
    if deal_promo:
        context['deal_promo'] = deal_promo

    return render(request, 'product_detail.html', context)


@require_POST
def add_to_cart(request, slug):
    # Fetch product allowing sold-out (we validate stock below)
    product = get_object_or_404(Product.objects.filter(aktivan=True).select_related('kategorija'), slug=slug)
    cart = Cart(request)
    variation = None
    variation_id = request.POST.get('variation_id', '').strip()
    quantity = max(1, int(request.POST.get('quantity', 1) or 1))

    stay_on_page = request.POST.get('stay') == '1'

    if not product.na_stanju and not product.varijacije.exists():
        msg = 'Artikal je rasprodan.'
        if stay_on_page:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('product_detail', slug=slug)

    if product.varijacije.count() > 0:
        if not variation_id:
            if stay_on_page:
                return JsonResponse({'ok': False, 'message': 'Odaberite varijantu.'}, status=400)
            messages.error(request, 'Odaberite varijantu prije dodavanja u korpu.')
            return redirect('product_detail', slug=slug)
        variation = get_object_or_404(
            ProductVariation, pk=variation_id, artikal=product, na_stanju=True,
        )
    elif variation_id:
        variation = get_object_or_404(
            ProductVariation, pk=variation_id, artikal=product, na_stanju=True,
        )

    # Double-check stock on the chosen item
    if variation and not variation.na_stanju:
        msg = 'Varijanta je rasprodana.'
        if stay_on_page:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('product_detail', slug=slug)
    if not variation and not product.na_stanju:
        msg = 'Artikal je rasprodan.'
        if stay_on_page:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('product_detail', slug=slug)

    cart.add(product, variation=variation, quantity=quantity)
    cart.clear_coupon()
    label = variation.naziv if variation else product.naziv
    message = f'"{label}" je dodano u korpu.'

    # Trigger upsell check
    _check_and_set_pending_upsell(request, product)

    if stay_on_page:
        return JsonResponse({
            'ok': True,
            'message': message,
            'cart_count': len(cart),
        })
    messages.success(request, message)
    if request.POST.get('redirect_to') == 'cart':
        return redirect('cart')
    return redirect('product_detail', slug=slug)


@require_POST
def add_upsell_to_cart(request, offer_id, product_id):
    offer = get_object_or_404(UpsellOffer, pk=offer_id, aktivan=True)
    product = get_object_or_404(Product.objects.filter(aktivan=True, na_stanju=True), pk=product_id)

    # Validate product is in the offer
    if not offer.ponuda_artikli.filter(pk=product.pk).exists():
        messages.error(request, 'Ovaj artikal nije dio ponude.')
        return redirect('checkout' if request.POST.get('next') == 'checkout' else 'cart')

    variation = None
    var_id = request.POST.get('variation_id')
    if var_id:
        try:
            variation = ProductVariation.objects.get(
                pk=int(var_id), artikal=product, na_stanju=True
            )
        except (ProductVariation.DoesNotExist, ValueError):
            messages.error(request, 'Nevažeća varijacija.')
            return redirect('checkout' if request.POST.get('next') == 'checkout' else 'cart')

    # Compute price with discount
    base_price = variation.prikazna_cijena if variation else product.prikazna_cijena
    final_price = base_price
    if offer.popust_postotak:
        final_price = (base_price * (Decimal('1') - offer.popust_postotak / Decimal('100'))).quantize(Decimal('0.01'))
    if offer.popust_km:
        final_price = max(Decimal('0'), final_price - offer.popust_km).quantize(Decimal('0.01'))

    cart = Cart(request)
    cart.add(product, variation=variation, quantity=1, custom_price=final_price)

    if offer.prikaz == UpsellOffer.PrikazTip.POPUP:
        from .upsell import clear_upsell_offer_session
        clear_upsell_offer_session(request)

    label = variation.naziv if variation else product.naziv
    messages.success(request, f'"{product.naziv} - {label}" je dodato u korpu sa specijalnom ponudom!')
    if request.POST.get('next') == 'checkout':
        return redirect('checkout')
    return redirect('cart')


def _check_and_set_pending_upsell(request, added_product):
    """Pokreni popup upsell samo kad se u korpu doda trigger artikal ili artikal iz trigger kategorije."""
    from django.db.models import Q

    from .upsell import set_upsell_offer_session

    try:
        offers = (
            UpsellOffer.objects.filter(
                aktivan=True,
                prikaz=UpsellOffer.PrikazTip.POPUP,
            )
            .filter(Q(trigger_artikal__isnull=False) | Q(trigger_kategorija__isnull=False))
            .order_by('redoslijed', 'id')
            .select_related('trigger_artikal', 'trigger_kategorija')
        )
        for offer in offers:
            triggered = False
            if offer.trigger_artikal_id == added_product.pk:
                triggered = True
            elif offer.trigger_kategorija_id and added_product.kategorija_id:
                trigger_cat = offer.trigger_kategorija
                if added_product.kategorija_id in trigger_cat.get_descendant_ids():
                    triggered = True
            if triggered:
                set_upsell_offer_session(request, offer.pk)
                break
    except Exception:
        pass


def _loyalty_za_kupon(request):
    if not request.user.is_authenticated:
        return None
    card = getattr(request.user, 'loyalty_kartica', None)
    if card is None:
        card = osiguraj_loyalty_karticu(request.user)
    return card


def _cart_context(request, cart):
    loyalty_card = _loyalty_za_kupon(request)
    applied_code = cart.get_coupon_code()
    summary = izracunaj_sazetak(
        cart.ukupno,
        user=request.user,
        coupon_code=applied_code,
    )
    cart_items = list(cart)
    if cart_items:
        slug_map = dict(
            Product.objects.filter(
                pk__in={item['product_id'] for item in cart_items},
            ).values_list('pk', 'slug'),
        )
        for item in cart_items:
            item['slug'] = item.get('slug') or slug_map.get(item['product_id'], '')
    return {
        'cart': cart,
        'cart_items': cart_items,
        'cart_total': summary['ukupno'],
        'summary': summary,
        'pricing': summary['pdv'],
        'coupon_form': CouponForm(initial={'kod': ''}),
        'applied_coupon_code': applied_code if cart.is_coupon_applied() else '',
        'loyalty_card': loyalty_card,
    }


def cart_view(request):
    from .upsell import get_cart_banner_upsell_offers

    cart = Cart(request)
    if not cart.should_keep_coupon_on_cart_view():
        cart.clear_coupon()
    elif not cart.is_coupon_applied() and cart.request.session.get(Cart.COUPON_KEY):
        cart.clear_coupon()
    context = {
        **_base_context(),
        **_cart_context(request, cart),
        'upsell_banners_above': get_cart_banner_upsell_offers(UpsellOffer.PrikazTip.BANNER_IZNAD),
        'upsell_banners_below': get_cart_banner_upsell_offers(UpsellOffer.PrikazTip.BANNER_ISPOD),
    }
    return render(request, 'cart.html', context)


@require_POST
def update_cart(request):
    cart = Cart(request)
    for key in list(cart.cart.keys()):
        qty = request.POST.get(f'quantity_{key}')
        if qty is not None:
            try:
                cart.set_quantity(key, int(qty))
            except (TypeError, ValueError):
                pass
    cart.clear_coupon()
    return redirect('cart')


@require_POST
def apply_coupon(request):
    cart = Cart(request)
    form = CouponForm(request.POST)
    if form.is_valid():
        kod = form.cleaned_data['kod']
        coupon, error = validiraj_kupon(kod, request.user)
        if error:
            messages.error(request, error)
        else:
            cart.set_coupon_code(coupon.kod)
            cart.mark_coupon_keep_after_apply()
            messages.success(request, f'Kupon {coupon.kod} primijenjen — popust {coupon.postotak}%.')
    else:
        for error in form.errors.get('kod', []):
            messages.error(request, error)
    redirect_to = request.POST.get('next', 'cart')
    if redirect_to == 'checkout':
        return redirect('checkout')
    return redirect('cart')


@require_POST
def remove_coupon(request):
    cart = Cart(request)
    cart.clear_coupon()
    messages.info(request, 'Kupon je uklonjen.')
    redirect_to = request.POST.get('next', 'cart')
    if redirect_to == 'checkout':
        return redirect('checkout')
    return redirect('cart')


@require_POST
def remove_from_cart(request, key):
    cart = Cart(request)
    cart.remove(key)
    cart.clear_coupon()
    messages.info(request, 'Artikal je uklonjen iz korpe.')
    return redirect('cart')


def _checkout_initial(request):
    if not request.user.is_authenticated:
        return {}
    profil = getattr(request.user, 'profil', None)
    return {
        'ime_prezime': request.user.get_full_name() or request.user.email,
        'email': request.user.email,
        'telefon': profil.telefon if profil else '',
        'adresa': profil.adresa if profil else '',
        'grad': profil.grad if profil else '',
        'postanski_broj': profil.postanski_broj if profil else '',
    }


def _save_profile_from_checkout(user, cleaned_data):
    profil, _ = UserProfile.objects.get_or_create(user=user)
    profil.telefon = cleaned_data['telefon']
    profil.adresa = cleaned_data['adresa']
    profil.grad = cleaned_data['grad']
    profil.postanski_broj = cleaned_data.get('postanski_broj', '')
    profil.save(update_fields=['telefon', 'adresa', 'grad', 'postanski_broj'])
    user.first_name = cleaned_data['ime_prezime']
    user.email = cleaned_data['email']
    user.save(update_fields=['first_name', 'email'])


def checkout(request):
    cart = Cart(request)
    if not cart.item_count:
        messages.warning(request, 'Korpa je prazna.')
        return redirect('home')

    form = CheckoutForm(initial=_checkout_initial(request))
    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            summary = cart.sazetak(user=request.user)
            order = Order.objects.create(
                korisnik=request.user if request.user.is_authenticated else None,
                ime_prezime=form.cleaned_data['ime_prezime'],
                email=form.cleaned_data['email'],
                telefon=form.cleaned_data['telefon'],
                adresa=form.cleaned_data['adresa'],
                grad=form.cleaned_data['grad'],
                postanski_broj=form.cleaned_data.get('postanski_broj', ''),
                napomena=form.cleaned_data.get('napomena', ''),
                medjuzbir=summary['medjuzbir'],
                dostava=summary['dostava'],
                popust=summary['popust'],
                kupon_kod=summary.get('kupon_kod', ''),
                ukupno=summary['ukupno'],
            )
            if request.user.is_authenticated:
                _save_profile_from_checkout(request.user, form.cleaned_data)
            for item in cart:
                product, variation = cart.get_product_and_variation(item)
                if not product:
                    messages.error(request, 'Neki artikli više nisu dostupni. Osvježite korpu.')
                    order.delete()
                    return redirect('cart')
                # Apply X+1 deal if present
                line_price = item['cijena_decimal']
                deal_info = item.get('deal_info')
                if deal_info and deal_info.get('has_discount'):
                    # Use effective per unit based on deal total
                    if item['quantity'] > 0:
                        line_price = (deal_info['deal_total'] / item['quantity']).quantize(Decimal('0.01'))

                # Apply AKCIJA popup discount if present
                akcija_info = item.get('akcija_popup_discount')
                if akcija_info and akcija_info.get('percent'):
                    pct = Decimal(str(akcija_info['percent']))
                    line_price = (item['cijena_decimal'] * (Decimal('1') - pct / Decimal('100'))).quantize(Decimal('0.01'))

                OrderItem.objects.create(
                    narudzba=order,
                    artikal=product,
                    varijacija=variation,
                    naziv=item['product_naziv'],
                    product_naziv=item['product_naziv'],
                    varijacija_naziv=item.get('varijacija_naziv', ''),
                    sifra=item['sifra'],
                    cijena=line_price,
                    kolicina=item['quantity'],
                )

            cart.clear()

            try:
                send_order_emails(order)
            except EmailNotConfiguredError:
                logger.error(
                    'Email nije konfigurisan — narudžba #%s nije poslana na %s.',
                    order.broj,
                    settings.ORDER_NOTIFICATION_EMAIL,
                )
                messages.warning(
                    request,
                    'Narudžba je sačuvana, ali email nije poslan. '
                    'Provjerite Proton SMTP postavke (EMAIL_APP_PASSWORD) na serveru.',
                )
            except Exception:
                logger.exception(
                    'Slanje emaila za narudžbu #%s nije uspjelo (cilj: %s).',
                    order.broj,
                    settings.ORDER_NOTIFICATION_EMAIL,
                )
                messages.warning(
                    request,
                    'Narudžba je sačuvana, ali email obavijest nije poslana. Kontaktirajte nas.',
                )

            # Sync loyalty nakon emaila — ne smije blokirati slanje narudžbe na mail
            logger.info("Checkout završen, pripremam sync za narudžbu #%s", order.broj)
            if request.user.is_authenticated:
                azuriraj_loyalty_nakon_narudzbe(order)
                card = getattr(request.user, 'loyalty_kartica', None)
                if card:
                    logger.info(
                        "Automatski sync korisnik (kartica) za kupca %s nakon narudžbe",
                        request.user.email,
                    )
                    sync_korisnik(request.user)
            result = sync_narudzba(order)
            if result is None:
                logger.warning("sync_narudzba vratio None (vjerovatno SYNC nije aktivan)")
            elif isinstance(result, dict) and not result.get('ok', True):
                logger.error("sync_narudzba nije uspio: %s", result)

            messages.success(request, 'Narudžba je uspješno poslana!')
            return redirect('order_success', broj=order.broj)

    from .upsell import get_checkout_upsell_offers

    context = {
        **_base_context(),
        **_cart_context(request, cart),
        'form': form,
        'upsell_checkout_offers': get_checkout_upsell_offers(cart),
    }

    # Remove deal and popup discount info from checkout (they only work/shows in cart/product detail)
    for item in context.get('cart_items', []):
        if 'deal_info' in item:
            del item['deal_info']
        if 'akcija_popup_discount' in item:
            del item['akcija_popup_discount']

    return render(request, 'checkout.html', context)


def order_success(request, broj):
    order = get_object_or_404(Order, broj=broj)
    context = {
        **_base_context(),
        'order': order,
    }
    return render(request, 'order_success.html', context)


def verify_turnstile(token, request):
    secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
    if not secret or not token:
        return False
    try:
        response = requests.post(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data={
                'secret': secret,
                'response': token,
                'remoteip': request.META.get('REMOTE_ADDR', ''),
            },
            timeout=10,
        )
        result = response.json()
        return result.get('success', False)
    except Exception:
        return False


def register(request):
    if request.user.is_authenticated:
        return redirect('account')

    form = RegisterForm()
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            token = form.cleaned_data.get('cf_turnstile_response')
            secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
            if secret and not verify_turnstile(token, request):
                form.add_error(None, 'Turnstile provjera nije uspjela. Molimo pokušajte ponovo.')
            else:
                email = form.cleaned_data['email']
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=form.cleaned_data['lozinka'],
                    first_name=form.cleaned_data['ime_prezime'],
                    is_active=False,
                )
                UserProfile.objects.create(
                    user=user,
                    telefon=form.cleaned_data.get('telefon', ''),
                )
                Order.objects.filter(email__iexact=email, korisnik__isnull=True).update(korisnik=user)
                kreiraj_loyalty_karticu(user)
                logger.info("Register: sync_korisnik za novog korisnika %s", email)
                sync_korisnik(user)

                # Send activation email
                uid = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                activation_link = request.build_absolute_uri(
                    reverse('activate', kwargs={'uidb64': uid, 'token': token})
                )
                subject = 'Aktivirajte vaš nalog | opremazaribolov.ba'
                html_message = render_to_string('emails/activation_email.html', {
                    'user': user,
                    'activation_link': activation_link,
                    'site_name': 'opremazaribolov.ba',
                })
                plain_message = strip_tags(html_message)
                send_mail(
                    subject,
                    plain_message,
                    getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@opremazaribolov.ba'),
                    [user.email],
                    html_message=html_message,
                    fail_silently=False,
                )

                messages.success(request, 'Nalog je uspješno kreiran. Provjerite vaš email za aktivacioni link.')
                return redirect('login')

    context = {
        **_base_context(),
        'form': form,
        'turnstile_site_key': getattr(settings, 'TURNSTILE_SITE_KEY', ''),
    }
    return render(request, 'auth/register.html', context)


def activate(request, uidb64, token):
    UserModel = User
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = UserModel.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, UserModel.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        messages.success(request, 'Vaš nalog je aktiviran! Sada se možete prijaviti.')
        return redirect('login')
    else:
        messages.error(request, 'Aktivacioni link je nevažeći ili je istekao.')
        return redirect('register')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('account')

    next_url = request.GET.get('next', '') or request.POST.get('next', '')
    form = LoginForm(request=request)
    if request.method == 'POST':
        form = LoginForm(request.POST, request=request)
        if form.is_valid():
            token = form.cleaned_data.get('cf_turnstile_response')
            secret = getattr(settings, 'TURNSTILE_SECRET_KEY', '')
            if secret and not verify_turnstile(token, request):
                form.add_error(None, 'Turnstile provjera nije uspjela. Molimo pokušajte ponovo.')
            else:
                login(request, form.user)
                Order.objects.filter(
                    email__iexact=form.user.email,
                    korisnik__isnull=True,
                ).update(korisnik=form.user)
                osiguraj_loyalty_karticu(form.user)
                messages.success(request, 'Uspješno ste se prijavili.')
                redirect_to = request.POST.get('next') or next_url
                if redirect_to and redirect_to.startswith('/'):
                    return redirect(redirect_to)
                return redirect('account')

    context = {
        **_base_context(),
        'form': form,
        'next_url': next_url,
        'turnstile_site_key': getattr(settings, 'TURNSTILE_SITE_KEY', ''),
    }
    return render(request, 'auth/login.html', context)


def logout_view(request):
    logout(request)
    messages.info(request, 'Odjavljeni ste.')
    return redirect('home')


@login_required(login_url='login')
def account(request):
    profil, _ = UserProfile.objects.get_or_create(user=request.user)
    profile_form = ProfileForm(initial={
        'ime_prezime': request.user.get_full_name() or request.user.first_name,
        'email': request.user.email,
        'telefon': profil.telefon,
        'adresa': profil.adresa,
        'grad': profil.grad,
        'postanski_broj': profil.postanski_broj,
    })

    if request.method == 'POST':
        profile_form = ProfileForm(request.POST)
        if profile_form.is_valid():
            email = profile_form.cleaned_data['email'].strip().lower()
            if User.objects.filter(email__iexact=email).exclude(pk=request.user.pk).exists():
                messages.error(request, 'Email je već u upotrebi.')
            elif User.objects.filter(username__iexact=email).exclude(pk=request.user.pk).exists():
                messages.error(request, 'Email je već u upotrebi.')
            else:
                request.user.first_name = profile_form.cleaned_data['ime_prezime']
                request.user.email = email
                request.user.username = email
                request.user.save(update_fields=['first_name', 'email', 'username'])
                profil.telefon = profile_form.cleaned_data.get('telefon', '')
                profil.adresa = profile_form.cleaned_data.get('adresa', '')
                profil.grad = profile_form.cleaned_data.get('grad', '')
                profil.postanski_broj = profile_form.cleaned_data.get('postanski_broj', '')
                profil.save()
                logger.info("Profile update: sync_korisnik za %s", request.user.email)
                sync_korisnik(request.user)
                messages.success(request, 'Podaci naloga su ažurirani.')

    orders = (
        Order.objects.filter(korisnik=request.user)
        .prefetch_related('stavke')
        .order_by('-kreirana')
    )
    loyalty = loyalty_kontekst(osiguraj_loyalty_karticu(request.user))

    context = {
        **_base_context(),
        'profile_form': profile_form,
        'orders': orders,
        'loyalty': loyalty,
    }
    return render(request, 'account/index.html', context)


@login_required(login_url='login')
def account_order_detail(request, broj):
    order = get_object_or_404(
        Order.objects.prefetch_related('stavke'),
        broj=broj,
        korisnik=request.user,
    )
    context = {
        **_base_context(),
        'order': order,
        'summary': sazetak_iz_narudzbe(order),
        'pricing': order.pdv_pregled,
    }
    return render(request, 'account/order_detail.html', context)


def _superuser_required(user):
    return user.is_authenticated and user.is_superuser


def _normalize_phone_query(value):
    return re.sub(r'\D', '', value or '')


def _search_staff_orders(query):
    query = (query or '').strip()
    if not query:
        return Order.objects.none()

    qs = Order.objects.prefetch_related('stavke').order_by('-kreirana')
    broj = query.lstrip('#').strip()
    filters = Q(broj=broj) | Q(email__iexact=query)

    digits = _normalize_phone_query(query)
    filters |= Q(telefon__icontains=query)
    if digits and digits != query:
        filters |= Q(telefon__icontains=digits)

    return qs.filter(filters).distinct()


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_lookup(request):
    query = request.GET.get('q', '').strip()

    orders = []
    searched = False
    if query:
        searched = True
        orders = list(_search_staff_orders(query))
        if len(orders) == 1:
            return redirect('staff_order_detail', broj=orders[0].broj)

    context = {
        **_base_context(),
        'search_query': query,
        'orders': orders,
        'searched': searched,
    }
    return render(request, 'staff/order_lookup.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_detail(request, broj):
    order = get_object_or_404(
        Order.objects.prefetch_related('stavke'),
        broj=broj,
    )
    context = {
        **_base_context(),
        **get_order_email_context(order),
    }
    return render(request, 'staff/order_detail.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_admin_panel(request):
    context = {
        **_base_context(),
    }
    return render(request, 'staff/admin_panel.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_loyalty_system(request):
    from decimal import InvalidOperation
    from .loyalty import azuriraj_loyalty_karticu, loyalty_kontekst, osiguraj_loyalty_karticu

    q = (request.GET.get('q') or '').strip()
    cards = []
    selected_card = None
    user_orders = []
    loyalty_ctx = None
    edit_form = None
    searched = bool(q)

    if q:
        # Search LoyaltyCard by kod/barkod or user fields
        cards_qs = LoyaltyCard.objects.select_related('user', 'user__profil').filter(
            Q(kod__icontains=q) |
            Q(barkod__icontains=q) |
            Q(user__email__icontains=q) |
            Q(user__first_name__icontains=q) |
            Q(user__last_name__icontains=q) |
            Q(user__profil__telefon__icontains=q)
        ).order_by('-azurirana')[:30]

        cards = list(cards_qs)

        if cards:
            selected_card = cards[0]
            selected_card = osiguraj_loyalty_karticu(selected_card.user)
            loyalty_ctx = loyalty_kontekst(selected_card)

            user_orders = Order.objects.filter(korisnik=selected_card.user).prefetch_related('stavke').order_by('-kreirana')[:50]

            profil = getattr(selected_card.user, 'profil', None)

            # Evidentiraj kupovinu (manual purchase)
            if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu':
                try:
                    iznos = Decimal(request.POST.get('iznos', '0'))
                    if iznos > 0:
                        selected_card.ukupna_potrosnja += iznos
                        selected_card.save(update_fields=['ukupna_potrosnja'])
                        azuriraj_loyalty_karticu(selected_card)
                        selected_card = osiguraj_loyalty_karticu(selected_card.user)
                        loyalty_ctx = loyalty_kontekst(selected_card)
                        messages.success(request, f'Kupovina od {iznos} KM evidentirana.')
                        return redirect(f"{request.path}?q={q}")
                    else:
                        messages.error(request, 'Iznos mora biti veći od 0.')
                except (InvalidOperation, ValueError):
                    messages.error(request, 'Neispravan iznos.')

            if request.method == 'POST' and request.POST.get('action') == 'update_profile':
                edit_form = ProfileForm(request.POST)
                if edit_form.is_valid():
                    u = selected_card.user
                    ime_prezime = edit_form.cleaned_data.get('ime_prezime', '').strip()
                    if ime_prezime:
                        parts = ime_prezime.split(maxsplit=1)
                        u.first_name = parts[0]
                        u.last_name = parts[1] if len(parts) > 1 else ''
                    u.email = edit_form.cleaned_data.get('email', u.email).strip().lower()
                    u.save(update_fields=['first_name', 'last_name', 'email'])

                    if profil:
                        profil.telefon = edit_form.cleaned_data.get('telefon', '')
                        profil.adresa = edit_form.cleaned_data.get('adresa', '')
                        profil.grad = edit_form.cleaned_data.get('grad', '')
                        profil.postanski_broj = edit_form.cleaned_data.get('postanski_broj', '')
                        profil.save()

                    messages.success(request, 'Podaci su ažurirani.')
                    return redirect(f"{request.path}?q={q}")
                else:
                    messages.error(request, 'Greška pri ažuriranju.')
            else:
                initial = {
                    'ime_prezime': selected_card.user.get_full_name() or selected_card.user.first_name,
                    'email': selected_card.user.email,
                }
                if profil:
                    initial.update({
                        'telefon': profil.telefon,
                        'adresa': profil.adresa,
                        'grad': profil.grad,
                        'postanski_broj': profil.postanski_broj,
                    })
                edit_form = ProfileForm(initial=initial)

    context = {
        **_base_context(),
        'search_query': q,
        'searched': searched,
        'cards': cards,
        'selected_card': selected_card,
        'user_orders': user_orders,
        'loyalty': loyalty_ctx,
        'edit_form': edit_form,
    }
    return render(request, 'staff/loyalty_system.html', context)
