import json
import logging
import random
import re
import uuid
import requests
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlparse

from django.conf import settings
from .models import SiteSettings
from django import forms as django_forms
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.db.models import Case, Count, Max, Prefetch, Q, When
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.html import escape, mark_safe, strip_tags
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .cart import Cart
from .category_visibility import filter_categories_with_products, get_category_ids_with_products
from .loyalty import (
    azuriraj_loyalty_nakon_narudzbe,
    izdaj_loyalty_karticu,
    kreiraj_loyalty_karticu,
    loyalty_kontekst,
    osiguraj_loyalty_karticu,
    validiraj_kupon,
)
from .pricing import izracunaj_sazetak, pripremi_stavke_za_racun, sazetak_iz_narudzbe
from .emails import (
    EmailNotConfiguredError,
    get_order_email_context,
    send_order_emails,
)
from .olx_api import (
    OlxApiError,
    fetch_olx_conversation_thread,
    fetch_olx_conversations,
    olx_chat_configured,
    publish_product_to_olx,
)
from .render_sync import sync_korisnik, sync_narudzba
from .meta_conversions import (
    track_add_to_cart,
    track_initiate_checkout,
    track_purchase,
    track_view_content,
)
from .utils.images import image_field_dimensions

logger = logging.getLogger(__name__)
from .forms import (
    CheckoutForm,
    CouponForm,
    LoginForm,
    LoyaltyIssueForm,
    ProfileForm,
    RegisterForm,
)
from .models import (
    ActiveCartItem,
    CartRecoveryAlert,
    Banner,
    Brand,
    Category,
    HomeBrandShowcase,
    HomeCategoryShowcase,
    HomeFeaturedProduct,
    HomeNovoProduct,
    HomeVlog,
    LoyaltyCard,
    Order,
    OrderItem,
    Product,
    ProductImage,
    ProductVariation,
    SiteSettings,
    Tag,
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


def _request_is_superuser(request):
    return bool(
        request
        and getattr(request, 'user', None)
        and request.user.is_authenticated
        and request.user.is_superuser
    )


STAFF_EDIT_MODE_SESSION_KEY = 'staff_edit_mode'


def _staff_edit_mode_enabled(request):
    """
    Superuser edit mode on the storefront.
    Default True. When False, product pages hide admin tools (view as regular user).
    """
    if not _request_is_superuser(request):
        return False
    # Missing key → on (backwards compatible)
    return bool(request.session.get(STAFF_EDIT_MODE_SESSION_KEY, True))


def _can_view_out_of_stock(request=None):
    """
    Superuser vidi artikle van stanja samo kad je Edit mode UKLJUČEN.
    Edit off → isto kao običan kupac (samo na stanju).
    """
    return _staff_edit_mode_enabled(request)


def _product_queryset(request=None):
    qs = Product.objects.filter(aktivan=True)
    if not _can_view_out_of_stock(request):
        qs = qs.filter(na_stanju=True)
    return _prefetch_product_cards(qs)


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


def _filter_size_scope_qs(filter_params, base_qs=None, *, request=None):
    """
    QS iz kojeg se grade filteri veličina/dužina/debljina.
    Mora pratiti aktivni kontekst (noviteti, akcija, brend, kategorija…)
    da se npr. na Novitetima ne prikazuje „Debljina” ako ti artikli nemaju mm.
    """
    qs = base_qs if base_qs is not None else _product_queryset(request)
    if filter_params.get('q'):
        qs = _apply_search_filter(qs, filter_params['q'])
    if filter_params.get('akcija'):
        qs = _akcija_products_qs(qs)
    if filter_params.get('noviteti'):
        qs = qs.filter(je_novitet=True)
    if filter_params.get('brend'):
        brand = Brand.objects.filter(slug=filter_params['brend']).first()
        if brand:
            qs = qs.filter(brend_id=brand.pk)
        else:
            qs = qs.none()
    if filter_params.get('kategorija'):
        category = Category.objects.filter(
            slug=filter_params['kategorija'], aktivan=True,
        ).first()
        if category:
            qs = qs.filter(kategorija_id__in=category.get_descendant_ids())
        else:
            qs = qs.none()
    return qs


def _filter_reset_url(filter_action, filter_params):
    preserved = {}
    if filter_params.get('akcija'):
        preserved['akcija'] = filter_params['akcija']
    if filter_params.get('noviteti'):
        preserved['noviteti'] = filter_params['noviteti']
    if filter_params.get('q'):
        preserved['q'] = filter_params['q']
    if filter_params.get('brend'):
        preserved['brend'] = filter_params['brend']
    if filter_params.get('kategorija'):
        preserved['kategorija'] = filter_params['kategorija']
    if filter_params.get('all'):
        preserved['all'] = filter_params['all']
    query = urlencode(preserved)
    if query:
        return f'{filter_action}?{query}'
    return filter_action


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
_SIZE_HASH_SUFFIX = re.compile(r'(?<![#/])\b(\d+(?:/\d+)?)#(?!\d)', re.I)
_SIZE_DIAMETER = re.compile(r'[Øø]\s*(\d+(?:[.,]\d+)?)', re.I)
_SIZE_PLAIN = re.compile(r'^\d+$')
_SIZE_CM = re.compile(r'(\d+(?:[.,]\d+)?)\s*cm\b', re.I)
_SIZE_M = re.compile(r'(\d+(?:[.,]\d+)?)\s*m\b', re.I)  # dužina najlona (ne mm)
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


def _variation_size_labels(naziv):
    """Vraća sve veličine iz naziva (#broj, cm, mm, g ili veličina mašinice)."""
    naziv = (naziv or '').strip()
    if not naziv:
        return []

    labels = []
    seen = set()

    def add(label):
        normalized = (label or '').strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            labels.append(normalized)

    if _SIZE_EXACT.match(naziv):
        add(naziv)
        return labels

    if _SIZE_PLAIN.match(naziv):
        add(naziv if naziv in _REEL_SIZES else f'#{naziv}')
        return labels

    for match in _SIZE_HASH.finditer(naziv):
        add(match.group(1))
    for match in _SIZE_HASH_SUFFIX.finditer(naziv):
        add(f'#{match.group(1)}')

    for match in _SIZE_CM.finditer(naziv):
        add(f'{_normalize_size_number(match.group(1))} cm')
    # Dužina u metrima (najlon). Pattern \bm ne hvata „mm” jer nema granice riječi između m-m.
    for match in _SIZE_M.finditer(naziv):
        add(f'{_normalize_size_number(match.group(1))} m')
    for match in _SIZE_GRAM.finditer(naziv):
        add(f'{_normalize_size_number(match.group(1))} g')
    for match in _SIZE_MM.finditer(naziv):
        add(f'{_normalize_size_number(match.group(1))} mm')

    for match in _SIZE_DIAMETER.finditer(naziv):
        value = _normalize_size_number(match.group(1))
        try:
            if float(value) < 10:
                add(f'{value} mm')
        except ValueError:
            continue

    if not labels:
        reel_match = _REEL_SIZE_PATTERN.search(naziv)
        if reel_match:
            add(reel_match.group(1))

    return labels


def _variation_size_label(naziv):
    labels = _variation_size_labels(naziv)
    return labels[0] if labels else None


def _size_sort_key(label):
    label = label or ''
    hook_match = re.search(r'#(\d+)', label)
    if hook_match:
        return (0, int(hook_match.group(1)), label)
    unit_match = re.match(r'^(\d+(?:\.\d+)?)\s*(m|cm|mm|g)$', label, re.I)
    if unit_match:
        unit = unit_match.group(2).lower()
        unit_rank = {'m': 1, 'cm': 2, 'mm': 3, 'g': 4}.get(unit, 9)
        return (unit_rank, float(unit_match.group(1)), label)
    if label.isdigit():
        return (3, int(label), label)
    return (9, 0, label)


_SIZE_FILTER_GROUPS = (
    ('duzina', 'Dužina (m / cm)', 'Prikaži sve artikle (ukloni dužinu)'),
    ('debljina', 'Debljina (mm)', 'Prikaži sve artikle (ukloni debljinu)'),
    ('gramaza', 'Gramaža (g)', 'Prikaži sve artikle (ukloni gramažu)'),
    ('velicina', 'Veličina (#)', 'Prikaži sve artikle (ukloni veličinu)'),
)


def _size_filter_group_key(label):
    label = (label or '').strip()
    if re.match(r'^\d+(?:\.\d+)?\s*(?:m|cm)$', label, re.I):
        return 'duzina'
    if re.match(r'^\d+(?:\.\d+)?\s*mm$', label, re.I):
        return 'debljina'
    if re.match(r'^\d+(?:\.\d+)?\s*g$', label, re.I):
        return 'gramaza'
    if label.startswith('#') or label in _REEL_SIZES or label.isdigit():
        return 'velicina'
    return 'velicina'


def _available_sizes(products_qs):
    sizes = set()
    nazivi = ProductVariation.objects.filter(
        artikal__in=products_qs,
        na_stanju=True,
    ).values_list('naziv', flat=True)
    for naziv in nazivi:
        sizes.update(_variation_size_labels(naziv))

    for naziv in Product.objects.filter(
        pk__in=products_qs.values('pk'),
        na_stanju=True,
    ).annotate(
        variation_count=Count('varijacije'),
    ).filter(variation_count=0).values_list('naziv', flat=True):
        sizes.update(_variation_size_labels(naziv))

    return sorted(sizes, key=_size_sort_key)


def _product_matches_size(product, size_label):
    if any(
        variation.na_stanju and size_label in _variation_size_labels(variation.naziv)
        for variation in product.varijacije.all()
    ):
        return True
    if getattr(product, 'variation_count', 0) == 0:
        return product.na_stanju and size_label in _variation_size_labels(product.naziv)
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
        'noviteti': request.GET.get('noviteti', '').strip(),
    }


_CATALOG_SCOPE_KEYS = frozenset({'all'})


def _filters_active(params):
    return any(value for key, value in params.items() if key not in _CATALOG_SCOPE_KEYS and value)


def _category_catalog_url_params(filter_params, *, keep_all_products):
    params = dict(filter_params)
    if keep_all_products:
        params['all'] = '1'
    return params


def _filter_categories():
    return filter_categories_with_products(
        Category.objects.filter(aktivan=True).select_related(
            'roditelj', 'roditelj__roditelj',
        ),
    ).order_by('redoslijed', 'naziv')


def _category_subnav_items(category, *, show_all_active=False):
    populated_category_ids = get_category_ids_with_products()
    items = []

    if category.roditelj_id:
        parent = category.roditelj
        siblings = list(
            filter_categories_with_products(
                Category.objects.filter(roditelj=parent, aktivan=True),
                populated_category_ids,
            ).order_by('redoslijed', 'naziv'),
        )
        if not siblings:
            return items
        parent_url = parent.get_absolute_url()
        items.append({
            'label': f'Sve u {parent.naziv}',
            'url': f'{parent_url}?all=1',
            'active': show_all_active and category.pk == parent.pk,
        })
        for sub in siblings:
            items.append({
                'label': sub.naziv,
                'url': sub.get_absolute_url(),
                'active': sub.pk == category.pk,
            })
        return items

    direct_subs = list(
        filter_categories_with_products(
            category.podkategorije.filter(aktivan=True),
            populated_category_ids,
        ).order_by('redoslijed', 'naziv'),
    )
    if not direct_subs:
        return items

    base_url = category.get_absolute_url()
    items.append({
        'label': f'Sve u {category.naziv}',
        'url': f'{base_url}?all=1',
        'active': show_all_active,
    })
    for sub in direct_subs:
        items.append({
            'label': sub.naziv,
            'url': sub.get_absolute_url(),
            'active': False,
        })
    return items


def _filter_banners_for_empty_categories(banners, populated_ids=None):
    populated_ids = populated_ids or get_category_ids_with_products()
    return [
        banner for banner in banners
        if not banner.kategorija_id or banner.kategorija_id in populated_ids
    ]


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


def _product_lager_priority(product):
    """0=normal, 1=favorizuj, 2=hit redukovanje — samo za sort među relevantnim."""
    try:
        return int(getattr(product, 'prioritet_lagera', 0) or 0)
    except (TypeError, ValueError):
        return 0


def _search_relevance_score(product, query):
    """Veći = bolje poklapanje (unutar već filtriranih rezultata)."""
    q = (query or '').strip().lower()
    if not q:
        return 0
    score = 0
    name = (product.naziv or '').lower()
    sifra = (product.sifra or '').lower()
    tokens = [t for t in re.split(r'\s+', q) if t]

    if sifra and (sifra == q or q in sifra):
        score += 120
    if name == q:
        score += 100
    elif name.startswith(q):
        score += 80
    elif q in name:
        score += 50

    for tok in tokens:
        if tok in name:
            score += 12
        if sifra and tok in sifra:
            score += 18

    cat = getattr(product, 'kategorija', None)
    if cat is not None:
        cat_name = (getattr(cat, 'naziv', None) or '').lower()
        if cat_name and (q in cat_name or any(t in cat_name for t in tokens)):
            score += 20
    return score


def _sort_products_by_lager_priority(products, *, query='', price_sort=None):
    """
    Katalog / pretraga / kategorija:
    1) Hit redukovanje lagera → Favorizuj → Normal
    2) Unutar nivoa: cijena rastuće (jeftinije → skuplje), osim opadajuce.
    """
    if not products:
        return products

    def key(p):
        prio = _product_lager_priority(p)
        name = (p.naziv or '').lower()
        try:
            price = float(_effective_product_price(p) or 0)
        except Exception:
            price = 0.0
        # Opadajuća: prioritet, pa skuplje → jeftinije
        if price_sort == 'opadajuca':
            return (-prio, -price, name)
        # Zadano + rastuća: prioritet, pa jeftinije → skuplje
        return (-prio, price, name)

    return sorted(products, key=key)


def _order_qs_by_lager_priority(qs, *extra_order):
    """QuerySet: prioritet_lagera DESC, zatim dodatni order_by."""
    return qs.order_by('-prioritet_lagera', *extra_order)


def _weighted_home_product_order(products):
    """
    Početna (bez filtera): prioritetni artikli češće gore,
    ali i dalje nasumično unutar nivoa (ne uvijek isti redoslijed).
    """
    if not products:
        return products
    buckets = {0: [], 1: [], 2: []}
    for p in products:
        prio = _product_lager_priority(p)
        if prio not in buckets:
            prio = 0
        buckets[prio].append(p)
    for prio in buckets:
        random.shuffle(buckets[prio])
    # Hit prvo, pa favorizuj, pa normal — unutar grupe shuffle
    ordered = buckets[2] + buckets[1] + buckets[0]
    # Blago miješanje susjednih da nije kruto, ali hit ostaje ispred normalnih
    # (ne miješamo preko granica prioriteta — korisnik želi prednost)
    return ordered


SEARCH_SUGGEST_LIMIT = 6
STAFF_LOOKUP_LIMIT = 25


def search_suggest(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': [], 'query': '', 'has_more': False})

    products_qs = _apply_search_filter(_product_queryset(request), query)
    products = list(products_qs[: max(SEARCH_SUGGEST_LIMIT * 4, 24)])
    products = _sort_products_by_lager_priority(products, query=query)
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

    if params['noviteti']:
        products = [product for product in products if getattr(product, 'je_novitet', False)]

    if params['velicina']:
        size_label = params['velicina']
        products = [
            product for product in products
            if _product_matches_size(product, size_label)
        ]

    # Redukovanje lagera prvo, zatim cijena (zadano = rastuća)
    if params['sort'] == 'opadajuca':
        price_sort = 'opadajuca'
    else:
        # prazno / rastuca / bilo šta drugo → rastuća cijena unutar prioriteta
        price_sort = 'rastuca'
    products = _sort_products_by_lager_priority(
        products,
        query=params.get('q') or '',
        price_sort=price_sort,
    )

    return products, params


CATALOG_PRODUCTS_PER_PAGE = 49
HOME_PRODUCTS_PER_PAGE = CATALOG_PRODUCTS_PER_PAGE
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
        return f'{filter_action}?{query}'
    return filter_action


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
            'key': key,
            'label': title,
            'options': options,
            'has_selection': any(option['selected'] for option in options),
            'clear_url': (
                _build_filter_url(filter_action, filter_params, velicina='')
                if selected_group == key else ''
            ),
            'clear_label': clear_label,
        })
    return groups


def _paginate_catalog_products(request, products, *, per_page=CATALOG_PRODUCTS_PER_PAGE):
    page_number = request.GET.get('page', '1')
    paginator = Paginator(products, per_page)
    try:
        return paginator.page(page_number)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages or 1)


def _paginate_home_products(request, products, filter_params):
    filters_active = _filters_active(filter_params)
    filter_signature = _catalog_query_string(filter_params)

    if filters_active:
        if request.session.get(HOME_FILTER_KEY) != filter_signature:
            request.session.pop(HOME_PRODUCT_ORDER_KEY, None)
            request.session[HOME_FILTER_KEY] = filter_signature
            request.session.modified = True
        # Filter/pretraga: redoslijed već postavljen u _apply_product_filters (lager prioritet)
    else:
        request.session.pop(HOME_FILTER_KEY, None)
        fresh_visit = 'page' not in request.GET
        if fresh_visit:
            products = _weighted_home_product_order(products)
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
            else:
                products = _weighted_home_product_order(products)

    return _paginate_catalog_products(request, products)


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
HOME_CATEGORY_SHOWCASE_LIMIT = 4
HOME_VLOG_LIMIT = 3


def _home_latest_products(request=None):
    """
    Noviteti na početnoj:
    1) Artikli označeni „Noviteti” (je_novitet) — prioritet
    2) Ručni odabir (HomeNovoProduct) ako je mod manual
    3) Fallback: zadnjih 10 unesenih
    """
    base_qs = _product_queryset(request)
    marked = list(
        _order_qs_by_lager_priority(
            base_qs.filter(je_novitet=True),
            '-kreiran', '-id',
        )[:HOME_SECTION_PRODUCT_LIMIT],
    )
    if marked:
        return marked

    site_settings = SiteSettings.load()
    if site_settings.noviteti_mod == SiteSettings.NovitetiMod.MANUAL:
        entries_qs = HomeNovoProduct.objects.filter(
            aktivan=True,
            artikal__aktivan=True,
        )
        if not _can_view_out_of_stock(request):
            entries_qs = entries_qs.filter(artikal__na_stanju=True)
        entries = entries_qs.select_related(
            'artikal', 'artikal__kategorija', 'artikal__brend',
        ).prefetch_related(
            Prefetch('artikal__varijacije', queryset=_in_stock_variations_qs()),
        ).order_by(
            '-artikal__prioritet_lagera', 'redoslijed', '-id',
        )[:HOME_SECTION_PRODUCT_LIMIT]
        products = [entry.artikal for entry in entries]
        if products:
            return products
    return list(
        _order_qs_by_lager_priority(base_qs, '-kreiran')[:HOME_SECTION_PRODUCT_LIMIT],
    )


def _home_featured_products(request=None):
    """
    Izdvojeni na početnoj:
    1) Artikli označeni „HIT / Izdvojeno” (je_hit)
    2) Fallback: ručni HomeFeaturedProduct
    Među njima: redukovanje lagera ima prednost.
    """
    base_qs = _product_queryset(request)
    marked = list(
        _order_qs_by_lager_priority(
            base_qs.filter(je_hit=True),
            '-kreiran', '-id',
        )[:HOME_SECTION_PRODUCT_LIMIT],
    )
    if marked:
        return marked

    entries_qs = HomeFeaturedProduct.objects.filter(
        aktivan=True,
        artikal__aktivan=True,
    )
    if not _can_view_out_of_stock(request):
        entries_qs = entries_qs.filter(artikal__na_stanju=True)
    entries = entries_qs.select_related(
        'artikal', 'artikal__kategorija', 'artikal__brend',
    ).prefetch_related(
        Prefetch('artikal__varijacije', queryset=_in_stock_variations_qs()),
    ).order_by(
        '-artikal__prioritet_lagera', 'redoslijed', '-id',
    )[:HOME_SECTION_PRODUCT_LIMIT]
    return [entry.artikal for entry in entries]


def _home_category_showcases(request=None):
    entries = HomeCategoryShowcase.objects.filter(
        aktivan=True,
        kategorija__aktivan=True,
    ).select_related('kategorija').order_by('redoslijed', 'id')

    sections = []
    for entry in entries:
        category_ids = entry.kategorija.get_descendant_ids()
        products = list(
            _order_qs_by_lager_priority(
                _product_queryset(request).filter(kategorija_id__in=category_ids),
                '-kreiran',
            )[:HOME_CATEGORY_SHOWCASE_LIMIT],
        )
        if not products:
            continue
        sections.append({
            'title': entry.display_title(),
            'category': entry.kategorija,
            'category_url': entry.kategorija.get_absolute_url(),
            'products': products,
        })
    return sections


def _home_brand_showcases(request=None):
    """
    Brend sekcije na početnoj — kao Noviteti / HIT: karusel artikala po brendu.
    Admin: Postavke sajta → Brendovi na početnoj (slide).
    """
    entries = HomeBrandShowcase.objects.filter(
        aktivan=True,
    ).select_related('brend').order_by('redoslijed', 'id')

    home_url = reverse('home')
    sections = []
    for entry in entries:
        products = list(
            _order_qs_by_lager_priority(
                _product_queryset(request).filter(brend_id=entry.brend_id),
                '-kreiran',
            )[:HOME_SECTION_PRODUCT_LIMIT],
        )
        if not products:
            continue
        brand = entry.brend
        brand_url = f'{home_url}?brend={brand.slug}#product-showcase'
        sections.append({
            'title': entry.display_title(),
            'brand': brand,
            'brand_url': brand_url,
            'products': products,
        })
    return sections


def _related_category_products(product, limit=HOME_SECTION_PRODUCT_LIMIT, request=None):
    """Slični / povezani — ista kategorija, prednost redukovanju lagera."""
    if not product.kategorija_id:
        return []
    return list(
        _order_qs_by_lager_priority(
            _product_queryset(request)
            .filter(kategorija_id=product.kategorija_id)
            .exclude(pk=product.pk),
            '-kreiran',
        )[:limit],
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
    grid_banners = _filter_banners_for_empty_categories(
        _banners_with_media(Banner.objects.filter(
            tip=Banner.BannerType.GRID, aktivan=True,
        ).select_related('kategorija').order_by('redoslijed', '-id'))[:8]
    )
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
    home_category_showcases = []
    home_brand_showcases = []
    home_vlogs = []
    page_obj = None
    search_products = []
    catalog_title = None
    catalog_subtitle = None
    filter_size_groups = []
    home_url = reverse('home')

    if filters_active:
        products, filter_params = _apply_product_filters(_product_queryset(request), request)
        scope_qs = _filter_size_scope_qs(filter_params, request=request)
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
        elif filter_params.get('noviteti'):
            catalog_title = 'Noviteti'
            if result_count:
                catalog_subtitle = f'{result_count} novih artikala.'
            else:
                catalog_subtitle = 'Trenutno nema označenih noviteta.'
        elif filter_params.get('brend'):
            brand = Brand.objects.filter(slug=filter_params['brend']).first()
            if brand:
                catalog_title = brand.naziv
                if result_count:
                    catalog_subtitle = f'{result_count} artikala brenda {brand.naziv}.'
                else:
                    catalog_subtitle = 'Nema artikala za odabrani brend.'
                if filter_params.get('velicina'):
                    catalog_subtitle = (
                        f'{catalog_subtitle} Filter: {filter_params["velicina"]}.'
                    )
        else:
            catalog_title = 'Rezultati'
            if result_count:
                catalog_subtitle = f'{result_count} artikala.'
        if (
            filter_params.get('velicina')
            and not filter_params.get('brend')
            and not filter_params.get('q')
            and not filter_params.get('akcija')
            and not filter_params.get('noviteti')
        ):
            size_label = filter_params['velicina']
            group_key = _size_filter_group_key(size_label)
            group_name = next(
                (title for key, title, _ in _SIZE_FILTER_GROUPS if key == group_key),
                'Filter',
            )
            catalog_title = group_name
            if result_count:
                catalog_subtitle = f'{result_count} artikala — {size_label}.'
            else:
                catalog_subtitle = f'Nema artikala za {size_label}.'
    else:
        latest_products = _home_latest_products(request)
        featured_products = _home_featured_products(request)
        home_category_showcases = _home_category_showcases(request)
        home_brand_showcases = _home_brand_showcases(request)
        home_vlogs = _home_vlogs()

    first_hero = hero_banners.first()
    first_grid_banner = grid_banners[0] if grid_banners else None
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
        'home_category_showcases': home_category_showcases,
        'home_brand_showcases': home_brand_showcases,
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
    populated_category_ids = get_category_ids_with_products()
    direct_subs = list(
        filter_categories_with_products(
            category.podkategorije.filter(aktivan=True),
            populated_category_ids,
        ).order_by('redoslijed', 'naziv')
    )
    filter_params = _get_filter_params(request)
    show_all = request.GET.get('all') == '1'
    show_products = show_all or _filters_active(filter_params)

    if direct_subs and not show_products:
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
    products_qs = _product_queryset(request).filter(kategorija_id__in=category_ids)
    filter_sizes = _available_sizes(products_qs)
    category_url = reverse('category', args=[category.slug])
    products, filter_params = _apply_product_filters(
        products_qs,
        request,
        allowed_category_ids=category_ids,
    )
    catalog_url_params = _category_catalog_url_params(
        filter_params,
        keep_all_products=bool(direct_subs),
    )

    page_obj = _paginate_catalog_products(request, products)

    context = {
        **_base_context(),
        'category': category,
        'products': page_obj.object_list,
        'page_obj': page_obj,
        'elided_page_range': page_obj.paginator.get_elided_page_range(page_obj.number),
        'catalog_query': _catalog_query_string(catalog_url_params),
        'filter_categories': _filter_categories(),
        'filter_params': filter_params,
        'filter_size_groups': _size_filter_groups(category_url, catalog_url_params, filter_sizes),
        'filter_reset_url': _filter_reset_url(category_url, catalog_url_params),
        'category_subnav': _category_subnav_items(category, show_all_active=show_all),
        'catalog_show_all': show_all,
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
    # Edit mode ON → superuser vidi i neaktivne / van stanja; Edit OFF → kao kupac
    if _can_view_out_of_stock(request):
        product_qs = Product.objects.all()
    else:
        product_qs = Product.objects.filter(aktivan=True, na_stanju=True)
    product = get_object_or_404(
        product_qs
        .select_related('kategorija', 'brend')
        .prefetch_related(
            Prefetch('varijacije', queryset=ProductVariation.objects.order_by('redoslijed', 'id')),
            Prefetch('dodatne_slike', queryset=ProductImage.objects.order_by('redoslijed', 'id')),
            'tagovi',
        ),
        slug=slug,
    )
    in_stock_variations = [v for v in product.varijacije.all() if v.na_stanju]
    lcp_image_url = None
    product_image_width, product_image_height = 800, 800
    if product.prikazna_slika:
        product_image_width, product_image_height = image_field_dimensions(
            product.prikazna_slika, default=(800, 800),
        )
        lcp_image_url = request.build_absolute_uri(product.prikazna_slika.url)

    related_products = _related_category_products(product, request=request)
    site_settings = SiteSettings.load()
    kategorija_naziv = product.kategorija.naziv if product.kategorija else ''

    context = {
        **_base_context(),
        'product': product,
        'in_stock_variations': in_stock_variations,
        'ima_varijacije': bool(in_stock_variations),
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
    from .gratis import build_gratis_offer_response, get_active_gratis_akcija_for_product

    deal_promo = get_deal_promo_data(product)
    if deal_promo:
        context['deal_promo'] = deal_promo

    gratis_akcija = get_active_gratis_akcija_for_product(product)
    if gratis_akcija and build_gratis_offer_response(gratis_akcija):
        # Samo tekstualni hint pored dugmeta; popup pri svakom dodavanju u korpu
        context['gratis_akcija_hint'] = True

    view_content_event_id = f'viewcontent-{product.pk}-{uuid.uuid4().hex[:12]}'
    context['meta_view_content_event_id'] = view_content_event_id
    track_view_content(request, product, event_id=view_content_event_id)

    from .product_urgency import build_product_urgency
    context['product_urgency'] = build_product_urgency(product)
    try:
        from .ai_conversion import product_conversion_boost
        context['conversion_boost'] = product_conversion_boost(product, request)
    except Exception:
        context['conversion_boost'] = None

    # AI dwell: flash cijena odmah na ulasku (bez popupa) — config za JS
    try:
        from .live_visitor_offer import (
            PRODUCT_DWELL_SECONDS,
            _product_dwell_settings,
            activate_product_dwell_flash,
            dwell_already_consumed,
            get_active_dwell_flash,
            get_dwell_flash_seconds,
            get_dwell_percent_for_product,
            product_allowed_for_dwell,
        )

        dwell_flash_seconds = get_dwell_flash_seconds()
        dwell_on, _default_pct = _product_dwell_settings()
        dwell_on_this = bool(dwell_on and product_allowed_for_dwell(product.pk))
        dwell_pct = get_dwell_percent_for_product(product.pk) if dwell_on_this else Decimal('0')
        is_staff = _request_is_superuser(request) or (
            getattr(request.user, 'is_authenticated', False)
            and getattr(request.user, 'is_staff', False)
        )
        # Samo eksplicitno ?dwell_force=1 (staff) smije obnoviti istekli flash
        force_dwell = bool(is_staff and request.GET.get('dwell_force') == '1')
        already_consumed = dwell_already_consumed(request, product.pk)
        dwell_flash = None
        activate_err = ''
        if dwell_on_this and dwell_pct and dwell_pct > 0:
            # Nastavi aktivni flash, ili aktiviraj jednom po sesiji
            dwell_flash = get_active_dwell_flash(request, product.pk)
            if not dwell_flash and (not already_consumed or force_dwell):
                dwell_flash, activate_err = activate_product_dwell_flash(
                    request,
                    product.pk,
                    force=force_dwell,
                )
            # Nema fallback-a — isteklo = regularna cijena i na refresh
        flash_json = None
        if dwell_flash and int(dwell_flash.get('remaining_seconds') or 0) > 0:
            pct = dwell_flash.get('percent')
            try:
                pct_f = float(pct)
            except (TypeError, ValueError):
                pct_f = 0
            flash_json = {
                'product_id': dwell_flash.get('product_id'),
                'percent': pct_f,
                'expires_ts': dwell_flash.get('expires_ts'),
                'remaining_seconds': dwell_flash.get('remaining_seconds') or dwell_flash_seconds,
                'base': dwell_flash.get('base'),
                'sale': dwell_flash.get('sale'),
            }
        try:
            pct_cfg = float(dwell_pct) if dwell_pct else 0
        except (TypeError, ValueError):
            pct_cfg = 0
        # active samo dok stvarno traje flash (ne pokreći JS aktivaciju poslije isteka)
        context['dwell_flash_config'] = {
            'active': bool(flash_json),
            'product_id': product.pk,
            'trigger_seconds': PRODUCT_DWELL_SECONDS,  # 0 = odmah
            'flash_seconds': dwell_flash_seconds,
            'percent': pct_cfg,
            'base_price': str(product.prikazna_cijena),
            'activate_url': '/ai-dwell/aktiviraj/',
            'flash': flash_json,
            'expired': bool(already_consumed and not flash_json),
            'staff_preview': False,
            'debug_err': activate_err if is_staff else '',
        }
    except Exception:
        context['dwell_flash_config'] = {'active': False}

    context['olx_configured'] = bool(settings.OLX_API_TOKEN)
    context['staff_product_tools'] = _staff_edit_mode_enabled(request)

    return render(request, 'product_detail.html', context)


@require_POST
def add_to_cart(request, slug):
    # Fetch product allowing sold-out (we validate stock below)
    product = get_object_or_404(Product.objects.filter(aktivan=True).select_related('kategorija'), slug=slug)
    cart = Cart(request)
    variation = None
    variation_id = request.POST.get('variation_id', '').strip()
    quantity = max(1, int(request.POST.get('quantity', 1) or 1))

    # AJAX (stay=1 ili XHR) — potreban za + Ponuda modal prije dodavanja u korpu
    stay_on_page = (
        request.POST.get('stay') == '1'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )

    if not product.na_stanju and not product.varijacije.exists():
        msg = 'Artikal je rasprodan.'
        if stay_on_page:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('product_detail', slug=slug)

    from .models import Akcija

    timer_akcija_from_popup = request.POST.get('akcija_id', '').strip()
    is_gratis_popup_add = bool(
        timer_akcija_from_popup
        and Akcija.objects.filter(
            pk=timer_akcija_from_popup,
            aktivan=True,
            tip=Akcija.Tip.GRATIS,
            gratis_popup=True,
            artikal_id=product.pk,
        ).exists()
    )
    is_qty_deal_popup_add = bool(
        timer_akcija_from_popup
        and Akcija.objects.filter(
            pk=timer_akcija_from_popup,
            aktivan=True,
            tip=Akcija.Tip.QTY_DEAL,
            artikal_id=product.pk,
        ).exists()
    )

    if product.varijacije.exists():
        if variation_id:
            variation = get_object_or_404(
                ProductVariation, pk=variation_id, artikal=product, na_stanju=True,
            )
        else:
            in_stock = product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id')
            if is_gratis_popup_add and in_stock.exists():
                variation = in_stock.first()
            elif is_qty_deal_popup_add and in_stock.exists():
                variation = in_stock.first()
            elif timer_akcija_from_popup and in_stock.count() == 1:
                variation = in_stock.first()
            elif stay_on_page:
                return JsonResponse({'ok': False, 'message': 'Odaberite varijantu.'}, status=400)
            else:
                messages.error(request, 'Odaberite varijantu prije dodavanja u korpu.')
                return redirect('product_detail', slug=slug)
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

    custom_price = None
    promo_bazna = None
    promo_akcija = None
    exit_popup_percent = None

    # AI dwell flash cijena (2 min snizenje na product page, bez popupa)
    try:
        from .live_visitor_offer import get_active_dwell_flash, _discounted_price
        dwell_deal = get_active_dwell_flash(request, product.pk)
        if dwell_deal and dwell_deal.get('percent'):
            base = variation.prikazna_cijena if variation else product.prikazna_cijena
            custom_price = _discounted_price(base, dwell_deal['percent'])
            promo_bazna = base
            # mark for cart — source set at cart.add below
            request._dwell_discount_percent = dwell_deal['percent']
    except Exception:
        pass

    if request.POST.get('exit_popup') == '1':
        from .cart_exit_popup import resolve_exit_popup_add

        exit_popup_add = resolve_exit_popup_add(request, product, variation)
        if not exit_popup_add:
            msg = 'Ponuda više nije dostupna.'
            if stay_on_page:
                return JsonResponse({'ok': False, 'message': msg}, status=400)
            messages.error(request, msg)
            return redirect('product_detail', slug=slug)
        if exit_popup_add.get('variation') and variation is None:
            variation = exit_popup_add['variation']
        if exit_popup_add.get('custom_price') is not None:
            custom_price = exit_popup_add['custom_price']
            promo_bazna = exit_popup_add['promo_bazna']
        exit_popup_percent = exit_popup_add.get('percent')
    from .gratis import (
        _add_discounted_gratis_line,
        apply_gratis_bundle_from_popup,
        apply_popup_bundle_from_popup,
        apply_qty_deal_from_popup,
        build_gratis_choice_message,
        build_gratis_offer_response,
        build_gratis_popup_message,
        build_popup_bundle_message,
        build_qty_deal_message,
        get_active_gratis_akcija_for_product,
    )

    gratis_choice = request.POST.get('gratis_choice', '').strip()
    gratis_akcija_id = request.POST.get('gratis_akcija_id', '').strip()
    try:
        gratis_quantity = max(1, min(99, int(request.POST.get('gratis_quantity', 1) or 1)))
    except (TypeError, ValueError):
        gratis_quantity = 1

    akcija_id = request.POST.get('akcija_id', '').strip()
    if akcija_id:
        # Pop-up bundle: set artikala s istim % (submit s bilo kojim slugom iz seta)
        popup_bundle_akcija = (
            Akcija.objects.filter(
                pk=akcija_id,
                aktivan=True,
                tip=Akcija.Tip.BUNDLE,
            )
            .filter(
                Q(bundle_artikli=product)
                | Q(bundle_lines__product=product)
                | Q(artikal_id=product.pk)
                | Q(gratis_artikal_id=product.pk)
            )
            .filter(
                # % na setu ili barem na jednoj liniji
                Q(popust_postotak__isnull=False)
                | Q(bundle_lines__popust_postotak__isnull=False)
            )
            .prefetch_related('bundle_artikli', 'bundle_lines__product')
            .select_related('gratis_artikal', 'artikal')
            .distinct()
            .first()
        )
        if popup_bundle_akcija and popup_bundle_akcija.jos_traje():
            # Bundle set — smije se dodavati više puta (količina se sabira)
            quantity = max(1, int(quantity or 1))
            bundle_result = apply_popup_bundle_from_popup(
                cart, popup_bundle_akcija, quantity=quantity,
            )
            if bundle_result:
                cart.clear_coupon()
                message = build_popup_bundle_message(
                    popup_bundle_akcija, quantity=quantity,
                )
                add_to_cart_event_id = f'addtocart-{uuid.uuid4().hex}'
                track_add_to_cart(
                    request,
                    product,
                    variation=variation,
                    quantity=quantity,
                    event_id=add_to_cart_event_id,
                )
                _check_and_set_pending_upsell(request, product)
                if stay_on_page:
                    return JsonResponse({
                        'ok': True,
                        'message': message,
                        'cart_count': len(cart),
                        'upsell_html': '',
                        'meta_add_to_cart': {
                            'event_id': add_to_cart_event_id,
                            'content_id': product.sifra or str(product.pk),
                            'content_name': product.naziv,
                            'value': float(
                                (variation.prikazna_cijena if variation else product.prikazna_cijena)
                                * quantity
                            ),
                        },
                    })
                messages.success(request, message)
                return redirect('cart')

        # Kupi više: N komada istog artikla s tier %
        qty_deal_akcija = (
            Akcija.objects.filter(
                pk=akcija_id,
                aktivan=True,
                tip=Akcija.Tip.QTY_DEAL,
                artikal_id=product.pk,
            )
            .prefetch_related('qty_tiers')
            .select_related('artikal')
            .first()
        )
        if qty_deal_akcija and qty_deal_akcija.jos_traje():
            tier_id = request.POST.get('tier_id', '').strip()
            deal_result = apply_qty_deal_from_popup(
                cart,
                qty_deal_akcija,
                quantity=quantity,
                tier_id=tier_id or None,
                variation=variation,
            )
            if deal_result:
                cart.clear_coupon()
                deal_qty = deal_result['quantity']
                message = build_qty_deal_message(
                    qty_deal_akcija,
                    quantity=deal_qty,
                    popust_postotak=deal_result.get('popust_postotak'),
                )
                add_to_cart_event_id = f'addtocart-{uuid.uuid4().hex}'
                track_add_to_cart(
                    request,
                    product,
                    variation=variation,
                    quantity=deal_qty,
                    event_id=add_to_cart_event_id,
                )
                _check_and_set_pending_upsell(request, product)
                unit = deal_result.get('unit_price') or (
                    variation.prikazna_cijena if variation else product.prikazna_cijena
                )
                if stay_on_page:
                    return JsonResponse({
                        'ok': True,
                        'message': message,
                        'cart_count': len(cart),
                        'upsell_html': '',
                        'meta_add_to_cart': {
                            'event_id': add_to_cart_event_id,
                            'content_id': product.sifra or str(product.pk),
                            'content_name': product.naziv,
                            'value': float(unit * deal_qty),
                            'quantity': deal_qty,
                        },
                    })
                messages.success(request, message)
                return redirect('cart')

        gratis_bundle_akcija = Akcija.objects.filter(
            pk=akcija_id,
            aktivan=True,
            tip=Akcija.Tip.GRATIS,
            gratis_popup=True,
            artikal_id=product.pk,
            gratis_artikal__isnull=False,
            popust_postotak__isnull=False,
        ).select_related('gratis_artikal', 'artikal').first()
        if gratis_bundle_akcija and gratis_bundle_akcija.jos_traje():
            bundle_result = apply_gratis_bundle_from_popup(
                cart, gratis_bundle_akcija, quantity=quantity,
            )
            if bundle_result:
                cart.clear_coupon()
                message = build_gratis_popup_message(gratis_bundle_akcija)
                add_to_cart_event_id = f'addtocart-{uuid.uuid4().hex}'
                track_add_to_cart(
                    request,
                    product,
                    variation=variation,
                    quantity=quantity,
                    event_id=add_to_cart_event_id,
                )
                _check_and_set_pending_upsell(request, product)
                if stay_on_page:
                    return JsonResponse({
                        'ok': True,
                        'message': message,
                        'cart_count': len(cart),
                        'upsell_html': '',
                        'meta_add_to_cart': {
                            'event_id': add_to_cart_event_id,
                            'content_id': product.sifra or str(product.pk),
                            'content_name': product.naziv,
                            'value': float(
                                (variation.prikazna_cijena if variation else product.prikazna_cijena)
                                * quantity
                            ),
                            'quantity': quantity,
                        },
                    })
                messages.success(request, message)
                if request.POST.get('redirect_to') == 'cart':
                    return redirect('cart')
                return redirect('product_detail', slug=slug)

    if akcija_id:
        promo_akcija = Akcija.objects.filter(
            pk=akcija_id,
            aktivan=True,
            artikal_id=product.pk,
            tip__in=[Akcija.Tip.TIMER, Akcija.Tip.KORPA_NUDJENJE],
        ).first()
    if not promo_akcija and stay_on_page:
        promo_akcija = Akcija.objects.filter(
            aktivan=True,
            tip=Akcija.Tip.TIMER,
            artikal_id=product.pk,
            popust_postotak__isnull=False,
        ).order_by('redoslijed', '-id').first()
    if promo_akcija and promo_akcija.jos_traje():
        prikazna = variation.prikazna_cijena if variation else product.prikazna_cijena
        if promo_akcija.tip == Akcija.Tip.TIMER:
            snizena = promo_akcija.timer_snizena_cijena(product, variation=variation)
        else:
            snizena = promo_akcija.korpa_nudjenje_snizena_cijena(product, variation=variation)
        if snizena is not None:
            custom_price = snizena
            promo_bazna = prikazna

    if gratis_choice in ('yes', 'no') and gratis_akcija_id:
        # DA → trigger + ponuda artikal; NE → samo trigger artikal
        choice_akcija = Akcija.objects.filter(
            pk=gratis_akcija_id,
            aktivan=True,
            tip__in=Akcija.CART_OFFER_TIPS,
            artikal_id=product.pk,
            gratis_artikal__isnull=False,
        ).select_related('gratis_artikal').first()
        # Legacy gratis i dalje zahtijeva %
        if (
            choice_akcija
            and choice_akcija.tip == Akcija.Tip.GRATIS
            and choice_akcija.popust_postotak is None
        ):
            choice_akcija = None
        if choice_akcija and choice_akcija.jos_traje():
            g_src = None
            g_pct = None
            if custom_price is not None and promo_akcija:
                g_src = f'Akcija: {promo_akcija.naziv}'
                g_pct = promo_akcija.popust_postotak
            # 1) Uvijek dodaj artikal na kojem je + Ponuda (trigger)
            cart.add(
                product,
                variation=variation,
                quantity=quantity,
                custom_price=custom_price,
                promo_bazna=promo_bazna,
                discount_source=g_src,
                discount_percent=g_pct,
            )
            # 2) Samo ako DA — dodaj i ponudu artikal (s opcionalnim %)
            # Ponuda se može dodavati koliko puta korisnik hoće (dok je akcija aktivna)
            accepted = gratis_choice == 'yes'
            if accepted:
                _add_discounted_gratis_line(
                    cart,
                    choice_akcija,
                    choice_akcija.gratis_artikal,
                    quantity=gratis_quantity,
                )
            cart.clear_coupon()
            label = variation.naziv if variation else product.naziv
            message = build_gratis_choice_message(
                choice_akcija,
                accepted=accepted,
                trigger_label=label,
            )
            add_to_cart_event_id = f'addtocart-{uuid.uuid4().hex}'
            track_add_to_cart(
                request,
                product,
                variation=variation,
                quantity=quantity,
                event_id=add_to_cart_event_id,
            )
            _check_and_set_pending_upsell(request, product)
            if stay_on_page:
                return JsonResponse({
                    'ok': True,
                    'message': message,
                    'cart_count': len(cart),
                    'upsell_html': '',
                    'meta_add_to_cart': {
                        'event_id': add_to_cart_event_id,
                        'content_id': product.sifra or str(product.pk),
                        'content_name': product.naziv,
                        'value': float(
                            (variation.prikazna_cijena if variation else product.prikazna_cijena)
                            * quantity
                        ),
                        'quantity': quantity,
                    },
                })
            messages.success(request, message)
            if request.POST.get('redirect_to') == 'cart':
                return redirect('cart')
            return redirect('product_detail', slug=slug)

    if stay_on_page and not gratis_choice and not akcija_id:
        offer_akcija = get_active_gratis_akcija_for_product(product)
        # Uvijek iskači dok je + Ponuda aktivna (ne gasimo po sesiji)
        if offer_akcija:
            offer = build_gratis_offer_response(offer_akcija)
            if offer:
                # NE dodaj u korpu još — čekaj DA/NE u modalu
                return JsonResponse({
                    'ok': True,
                    'requires_gratis_choice': True,
                    'gratis_offer': offer,
                    'cart_count': len(cart),
                    'message': 'Odaberi: želiš li i + Ponudu?',
                })

    disc_src = None
    disc_pct = None
    if exit_popup_percent and exit_popup_percent > 0 and custom_price is not None:
        disc_src = f'Exit popup ponuda (−{exit_popup_percent}%)'
        disc_pct = exit_popup_percent
    elif custom_price is not None and promo_akcija:
        tip_label = promo_akcija.get_tip_display() if hasattr(promo_akcija, 'get_tip_display') else 'Akcija'
        pct = promo_akcija.popust_postotak
        if pct:
            disc_src = f'Akcija: {tip_label} „{promo_akcija.naziv}” (−{pct}%)'
            disc_pct = pct
        else:
            disc_src = f'Akcija: {tip_label} „{promo_akcija.naziv}”'
    elif custom_price is not None and getattr(request, '_dwell_discount_percent', None):
        dp = request._dwell_discount_percent
        disc_src = f'AI dwell flash (−{dp}%)'
        disc_pct = dp
    elif custom_price is not None:
        disc_src = 'Specijalna snižena cijena'

    cart.add(
        product,
        variation=variation,
        quantity=quantity,
        custom_price=custom_price,
        promo_bazna=promo_bazna,
        discount_source=disc_src,
        discount_percent=disc_pct,
    )
    cart.clear_coupon()
    if request.POST.get('exit_popup') == '1':
        from .cart_exit_popup import dismiss_cart_exit_popup

        dismiss_cart_exit_popup(request)
    label = variation.naziv if variation else product.naziv
    message = f'"{label}" je dodano u korpu.'
    if exit_popup_percent and exit_popup_percent > 0 and custom_price is not None:
        pct = int(exit_popup_percent) if exit_popup_percent == int(exit_popup_percent) else exit_popup_percent
        message = f'"{label}" je dodano u korpu sa {pct}% popusta.'
    elif custom_price is not None and promo_akcija and promo_akcija.popust_postotak:
        pct = int(promo_akcija.popust_postotak) if promo_akcija.popust_postotak == int(promo_akcija.popust_postotak) else promo_akcija.popust_postotak
        if promo_akcija.tip == Akcija.Tip.KORPA_NUDJENJE:
            message = f'"{label}" je dodano u korpu sa {pct}% popusta.'
        else:
            message = f'"{label}" je dodano u korpu sa {pct}% popusta (tajmer akcija).'

    add_to_cart_event_id = f'addtocart-{uuid.uuid4().hex}'
    content_id = (
        (variation.sifra if variation and variation.sifra else None)
        or product.sifra
        or str(product.pk)
    )
    line_price = custom_price if custom_price is not None else (
        variation.prikazna_cijena if variation else product.prikazna_cijena
    )
    cart_label = product.naziv
    if variation:
        cart_label = f'{product.naziv} — {variation.naziv}'
    track_add_to_cart(
        request,
        product,
        variation=variation,
        quantity=quantity,
        event_id=add_to_cart_event_id,
    )

    # Superuser toast: prihvaćena popup / AI / exit / akcija ponuda
    if custom_price is not None and not (
        request.user.is_authenticated and request.user.is_superuser
    ):
        try:
            from .cart_tracking import get_cart_session_key
            from .live_visitors import _display_email, _display_name
            from .models import LiveVisitor
            from .staff_alerts import notify_offer_accepted

            src = 'popup ponuda'
            pct = None
            if exit_popup_percent:
                src = 'exit popup (poslednja šansa)'
                pct = exit_popup_percent
            elif getattr(request, '_dwell_discount_percent', None):
                src = 'AI dwell flash'
                pct = request._dwell_discount_percent
            elif promo_akcija:
                src = f'akcija „{promo_akcija.naziv}”'
                pct = promo_akcija.popust_postotak
            sk = get_cart_session_key(request) or ''
            ime = _display_name(request.user if request.user.is_authenticated else None)
            email = _display_email(request.user if request.user.is_authenticated else None)
            grad = ''
            lv = LiveVisitor.objects.filter(session_key=sk).only('ime', 'email', 'grad').first()
            if lv:
                ime = (lv.ime or '').strip() or ime
                email = (lv.email or '').strip() or email
                grad = (lv.grad or '').strip()
            notify_offer_accepted(
                ime=ime,
                email=email,
                grad=grad,
                session_key=sk,
                product_name=cart_label,
                discount_percent=pct,
                source=src,
            )
        except Exception:
            pass

    # Trigger upsell check
    _check_and_set_pending_upsell(request, product)

    if stay_on_page:
        from django.template.loader import render_to_string

        from .upsell import get_active_upsell_offer

        upsell_html = ''
        upsell_offer = get_active_upsell_offer(request)
        if upsell_offer and upsell_offer.get('prikaz') == UpsellOffer.PrikazTip.POPUP:
            upsell_html = render_to_string(
                'partials/upsell_popup.html',
                {'active_upsell_offer': upsell_offer},
                request=request,
            )
        return JsonResponse({
            'ok': True,
            'message': message,
            'cart_count': len(cart),
            'upsell_html': upsell_html,
            'meta_add_to_cart': {
                'event_id': add_to_cart_event_id,
                'content_id': content_id,
                'content_name': cart_label,
                'value': float(line_price * quantity),
                'quantity': quantity,
            },
        })
    messages.success(request, message)
    if request.POST.get('redirect_to') == 'cart':
        return redirect('cart')
    return redirect('product_detail', slug=slug)


def _upsell_stay_on_page(request):
    return (
        request.POST.get('stay') == '1'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def _upsell_redirect_target(request):
    return 'checkout' if request.POST.get('next') == 'checkout' else 'cart'


def _upsell_add_error_response(request, message):
    if _upsell_stay_on_page(request):
        return JsonResponse({'ok': False, 'message': message}, status=400)
    messages.error(request, message)
    return redirect(_upsell_redirect_target(request))


def _checkout_summary_payload(request, cart):
    from .upsell import get_checkout_upsell_offers

    cart_items = list(cart)
    summary = cart.sazetak(user=request.user)
    for item in cart_items:
        item.pop('deal_info', None)
        item.pop('akcija_popup_discount', None)

    return {
        'checkout_items_html': render_to_string(
            'partials/checkout_items.html',
            {'cart_items': cart_items},
            request=request,
        ),
        'checkout_totals_html': render_to_string(
            'partials/order_totals_checkout.html',
            {'summary': summary},
            request=request,
        ),
        'checkout_upsell_html': render_to_string(
            'partials/upsell_checkout.html',
            {'upsell_offers': get_checkout_upsell_offers(cart)},
            request=request,
        ),
        'cart_total': str(summary['ukupno']),
    }


@require_POST
def add_upsell_to_cart(request, offer_id, product_id):
    offer = UpsellOffer.objects.filter(pk=offer_id, aktivan=True).first()
    if not offer:
        return _upsell_add_error_response(request, 'Ponuda više nije dostupna.')

    product = Product.objects.filter(aktivan=True, na_stanju=True, pk=product_id).first()
    if not product:
        return _upsell_add_error_response(request, 'Artikal nije dostupan.')

    if not offer.ponuda_artikli.filter(pk=product.pk).exists():
        return _upsell_add_error_response(request, 'Ovaj artikal nije dio ponude.')

    in_stock_variations = product.varijacije.filter(na_stanju=True)
    variation = None
    var_id = (request.POST.get('variation_id') or '').strip()
    if var_id:
        try:
            variation = in_stock_variations.get(pk=int(var_id))
        except (ProductVariation.DoesNotExist, ValueError):
            return _upsell_add_error_response(request, 'Nevažeća varijacija.')
    elif in_stock_variations.exists():
        return _upsell_add_error_response(request, 'Izaberite varijaciju.')

    base_price = variation.prikazna_cijena if variation else product.prikazna_cijena
    final_price = base_price
    if offer.popust_postotak:
        final_price = (base_price * (Decimal('1') - offer.popust_postotak / Decimal('100'))).quantize(Decimal('0.01'))
    if offer.popust_km:
        final_price = max(Decimal('0'), final_price - offer.popust_km).quantize(Decimal('0.01'))

    cart = Cart(request)
    up_src = f'Upsell ponuda „{offer.naziv}”'
    up_pct = offer.popust_postotak
    if up_pct:
        up_src = f'{up_src} (−{up_pct}%)'
    cart.add(
        product,
        variation=variation,
        quantity=1,
        custom_price=final_price,
        promo_bazna=base_price,
        discount_source=up_src,
        discount_percent=up_pct,
    )

    if not (request.user.is_authenticated and request.user.is_superuser):
        try:
            from .cart_tracking import get_cart_session_key
            from .live_visitors import _display_email, _display_name
            from .models import LiveVisitor
            from .staff_alerts import notify_offer_accepted

            sk = get_cart_session_key(request) or ''
            ime = _display_name(request.user if request.user.is_authenticated else None)
            email = _display_email(request.user if request.user.is_authenticated else None)
            grad = ''
            lv = LiveVisitor.objects.filter(session_key=sk).only('ime', 'email', 'grad').first()
            if lv:
                ime = (lv.ime or '').strip() or ime
                email = (lv.email or '').strip() or email
                grad = (lv.grad or '').strip()
            pname = product.naziv
            if variation:
                pname = f'{product.naziv} — {variation.naziv}'
            notify_offer_accepted(
                ime=ime,
                email=email,
                grad=grad,
                session_key=sk,
                product_name=pname,
                discount_percent=up_pct,
                source=f'upsell „{offer.naziv}”',
            )
        except Exception:
            pass

    stay_on_page = _upsell_stay_on_page(request)
    if offer.prikaz == UpsellOffer.PrikazTip.POPUP:
        from .upsell import mark_upsell_popup_consumed
        mark_upsell_popup_consumed(request)

    label = variation.naziv if variation else product.naziv
    success_message = f'"{product.naziv} - {label}" je dodato u korpu sa specijalnom ponudom!'
    if stay_on_page:
        payload = {
            'ok': True,
            'message': success_message,
            'cart_count': len(cart),
        }
        if request.POST.get('next') == 'checkout':
            payload.update(_checkout_summary_payload(request, cart))
        return JsonResponse(payload)
    messages.success(request, success_message)
    return redirect(_upsell_redirect_target(request))


@require_POST
def dismiss_upsell_popup(request):
    from .upsell import mark_upsell_popup_consumed

    mark_upsell_popup_consumed(request)
    return JsonResponse({'ok': True})


def _check_and_set_pending_upsell(request, added_product):
    """Pokreni popup upsell samo kad se u korpu doda trigger artikal ili artikal iz trigger kategorije."""
    from django.db.models import Q

    from .upsell import is_upsell_popup_consumed, set_upsell_offer_session

    if is_upsell_popup_consumed(request):
        return

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
    cart_items = list(cart)
    summary = cart.sazetak(user=request.user)
    applied_code = cart.get_coupon_code() if cart.is_coupon_applied() else ''
    if not applied_code:
        applied_code = summary.get('kupon_kod') or ''
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
        'applied_coupon_code': applied_code,
        'loyalty_card': loyalty_card,
    }


def cart_view(request):
    from django.db import OperationalError

    from .cart_tracking import sync_active_cart
    from .upsell import get_cart_banner_upsell_offers

    cart = Cart(request)
    try:
        sync_active_cart(request, cart)
    except OperationalError:
        # SQLite lock — prikaži korpu iz sesije bez staff track sync-a
        pass
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
            messages.success(
                request,
                f'Broj kartice {coupon.kod} primijenjen — popust {coupon.postotak}%.',
            )
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


@require_POST
def cart_recovery_apply(request):
    from .cart_recovery import apply_cart_recovery_discount

    cart = Cart(request)
    ok, result = apply_cart_recovery_discount(request, cart)
    if ok:
        if result and result > 0:
            pct = int(result) if result == int(result) else result
            messages.success(request, f'Popust od {pct}% je primijenjen na vašu korpu.')
        else:
            messages.info(request, 'Nastavite kupovinu u korpi.')
    else:
        messages.warning(request, result)
    return redirect('cart')


@require_POST
def cart_recovery_dismiss(request):
    from .cart_recovery import dismiss_cart_recovery_alert

    dismiss_cart_recovery_alert(request)
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('home')
    return redirect(next_url)


@require_POST
def cart_exit_dismiss(request):
    from .cart_exit_popup import dismiss_cart_exit_popup

    dismiss_cart_exit_popup(request)
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('cart')
    return redirect(next_url)


@require_POST
def cart_abandon_exit_dismiss(request):
    """Zatvori exit podsjetnik „imamo u korpi” (sesija)."""
    from .cart_exit_popup import dismiss_cart_abandon_exit

    dismiss_cart_abandon_exit(request)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('home')
    return redirect(next_url)


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
            popust_detalji = []
            for p_label in (summary.get('pogodnosti') or []):
                popust_detalji.append({'opis': str(p_label), 'iznos': None})
            if summary.get('kupon_popust'):
                popust_detalji.append({
                    'opis': f'Kupon {summary.get("kupon_kod") or ""}'.strip(),
                    'iznos': str(summary['kupon_popust']),
                })
            if summary.get('recovery_popust'):
                popust_detalji.append({
                    'opis': 'Poseban popust na korpu (recovery)',
                    'iznos': str(summary['recovery_popust']),
                })
            if summary.get('prize_popust'):
                popust_detalji.append({
                    'opis': 'Nagradni točak / online nagrada',
                    'iznos': str(summary['prize_popust']),
                })

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
                popust_detalji=popust_detalji,
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
                line_price = item['cijena_decimal']
                bazna = item.get('bazna_cijena_decimal')
                if bazna is None:
                    bazna = Decimal(str(item.get('bazna_cijena') or line_price))
                deal_info = item.get('deal_info')
                from .upsell import format_deal_order_note

                deal_note = format_deal_order_note(deal_info)
                akcija_info = item.get('akcija_popup_discount')
                discounted_unit = item.get('discounted_unit_price')
                popust_opis = (item.get('discount_source') or '').strip()
                popust_postotak = None
                raw_pct = item.get('discount_percent')
                if raw_pct not in (None, ''):
                    try:
                        popust_postotak = Decimal(str(raw_pct))
                    except Exception:
                        popust_postotak = None

                if deal_note:
                    naziv = item['product_naziv'] + deal_note
                    product_naziv = item['product_naziv'] + deal_note
                    varijacija_naziv = (item.get('varijacija_naziv', '') + deal_note).strip()
                    if not popust_opis and deal_info:
                        pct = deal_info.get('pct') or deal_info.get('percent')
                        vrsta = deal_info.get('vrsta') or deal_info.get('label') or 'Deal'
                        popust_opis = f'Deal {vrsta}' + (f' (−{pct}%)' if pct else '')
                        if pct and popust_postotak is None:
                            try:
                                popust_postotak = Decimal(str(pct))
                            except Exception:
                                pass
                elif akcija_info and discounted_unit is not None:
                    pct = Decimal(str(akcija_info['percent']))
                    disc_for_one = discounted_unit
                    extra_note = f" (popust iz akcije {pct}% na 1 kom. - sniženo na {disc_for_one} KM)"
                    naziv = item['product_naziv'] + extra_note
                    product_naziv = item['product_naziv'] + extra_note
                    varijacija_naziv = (item.get('varijacija_naziv', '') + extra_note).strip()
                    if not popust_opis:
                        aid = akcija_info.get('akcija_id')
                        popust_opis = f'Uslov prodaja / akcija #{aid}' if aid else 'Uslov prodaja'
                        popust_opis = f'{popust_opis} (−{pct}% na 1 kom.)'
                    popust_postotak = pct
                else:
                    naziv = item['product_naziv']
                    product_naziv = item['product_naziv']
                    varijacija_naziv = item.get('varijacija_naziv', '')
                    if not popust_opis and item.get('na_akciji') and bazna > line_price:
                        popust_opis = 'Katalog akcija (snižena cijena)'

                # Ušteda: regularna vs naplaćena
                qty = int(item['quantity'] or 1)
                charged_line = Decimal(str(item.get('ukupno_stavka') or (line_price * qty)))
                regular_line = (bazna * qty).quantize(Decimal('0.01'))
                popust_iznos = None
                if regular_line > charged_line:
                    popust_iznos = (regular_line - charged_line).quantize(Decimal('0.01'))
                elif discounted_unit is not None and bazna > discounted_unit:
                    popust_iznos = (bazna - discounted_unit).quantize(Decimal('0.01'))

                OrderItem.objects.create(
                    narudzba=order,
                    artikal=product,
                    varijacija=variation,
                    naziv=naziv,
                    product_naziv=product_naziv,
                    varijacija_naziv=varijacija_naziv,
                    sifra=item['sifra'],
                    cijena=line_price,
                    bazna_cijena=bazna,
                    popust_opis=popust_opis[:300] if popust_opis else '',
                    popust_postotak=popust_postotak,
                    popust_iznos=popust_iznos,
                    kolicina=qty,
                )

            try:
                from .online_gift import mark_reward_consumed
                mark_reward_consumed(request, order=order)
            except Exception:
                pass
            cart.clear()
            if request.user.is_authenticated:
                from .live_visitor_offer import consume_registration_reward
                consume_registration_reward(request.user)
            try:
                from .live_visitor_offer import clear_free_shipping_reward
                clear_free_shipping_reward(request, request.user if request.user.is_authenticated else None)
            except Exception:
                pass

            try:
                from .cart_tracking import get_cart_session_key
                from .staff_alerts import notify_purchase
                if not (request.user.is_authenticated and request.user.is_superuser):
                    notify_purchase(
                        ime=order.ime_prezime,
                        email=order.email,
                        grad=order.grad,
                        session_key=get_cart_session_key(request),
                        order_number=order.broj,
                        total=str(order.ukupno),
                    )
            except Exception:
                pass

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

            purchase_event_id = f'purchase-{order.broj}'
            track_purchase(request, order, event_id=purchase_event_id)
            request.session['meta_purchase_event_id'] = purchase_event_id
            request.session.modified = True

            messages.success(request, 'Narudžba je uspješno poslana!')
            success_url = reverse('order_success', kwargs={'broj': order.broj})
            return redirect(f'{success_url}?purchase=1')

    from .upsell import get_checkout_upsell_offers

    context = {
        **_base_context(),
        **_cart_context(request, cart),
        'form': form,
        'upsell_checkout_offers': get_checkout_upsell_offers(cart),
    }
    if request.method == 'GET':
        initiate_checkout_event_id = f'initiatecheckout-{uuid.uuid4().hex}'
        track_initiate_checkout(request, cart, event_id=initiate_checkout_event_id)
        context['meta_initiate_checkout_event_id'] = initiate_checkout_event_id

    # Remove deal and popup discount info from checkout (they only work/shows in cart/product detail)
    for item in context.get('cart_items', []):
        if 'deal_info' in item:
            del item['deal_info']
        if 'akcija_popup_discount' in item:
            del item['akcija_popup_discount']

    return render(request, 'checkout.html', context)


def order_success(request, broj):
    order = get_object_or_404(
        Order.objects.prefetch_related('stavke'),
        broj=broj,
    )
    purchase_event_id = request.session.pop('meta_purchase_event_id', None)
    track_purchase = request.GET.get('purchase') == '1'
    if track_purchase and not purchase_event_id:
        purchase_event_id = f'purchase-{order.broj}'
    stavke = list(order.stavke.all())
    purchase_contents = [
        {
            'id': stavka.sifra or str(stavka.artikal_id or stavka.pk),
            'quantity': stavka.kolicina,
            'item_price': float(stavka.cijena),
        }
        for stavka in stavke
    ]
    google_purchase_items = [
        {
            'item_id': stavka.sifra or str(stavka.artikal_id or stavka.pk),
            'item_name': stavka.puni_naziv,
            'price': float(stavka.cijena),
            'quantity': stavka.kolicina,
        }
        for stavka in stavke
    ]
    context = {
        **_base_context(),
        'order': order,
        'track_purchase': track_purchase,
        'meta_purchase_event_id': purchase_event_id if track_purchase else None,
        'meta_purchase_num_items': sum(stavka.kolicina for stavka in stavke),
        'meta_purchase_content_ids': ','.join(item['id'] for item in purchase_contents),
        'meta_purchase_contents': json.dumps(purchase_contents, ensure_ascii=False),
        'google_purchase_data': {
            'transaction_id': order.broj,
            'value': float(order.ukupno),
            'currency': 'BAM',
            'shipping': float(order.dostava),
            'items': google_purchase_items,
        },
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
                # Odmah aktivan — bez email aktivacije / bez slanja maila
                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=form.cleaned_data['lozinka'],
                    first_name=form.cleaned_data['ime_prezime'],
                    is_active=True,
                )
                UserProfile.objects.create(
                    user=user,
                    telefon=form.cleaned_data.get('telefon', ''),
                )
                Order.objects.filter(email__iexact=email, korisnik__isnull=True).update(korisnik=user)
                kreiraj_loyalty_karticu(user)
                logger.info("Register: sync_korisnik za novog korisnika %s", email)
                sync_korisnik(user)

                from .live_visitor_offer import claim_registration_invite_reward
                reg_reward = claim_registration_invite_reward(request, user)

                try:
                    from .cart_tracking import get_cart_session_key
                    from .staff_alerts import notify_registration
                    notify_registration(
                        ime=form.cleaned_data.get('ime_prezime') or '',
                        email=email,
                        session_key=get_cart_session_key(request),
                    )
                except Exception:
                    pass

                # Nagradna igra: ako je došao preko „Registruj se i igraj”, zadrži flag
                try:
                    from .online_gift import SESSION_AFTER_AUTH_KEY, mark_gift_registration_intent
                    if request.session.get(SESSION_AFTER_AUTH_KEY):
                        mark_gift_registration_intent(request)
                except Exception:
                    pass

                # Odmah prijavi korisnika (nema čekanja na email)
                from django.contrib.auth import login as auth_login
                auth_login(
                    request,
                    user,
                    backend='django.contrib.auth.backends.ModelBackend',
                )

                if reg_reward and reg_reward.get('percent'):
                    messages.success(
                        request,
                        f'Dobrodošli! Nalog je spreman. '
                        f'Imate {reg_reward["percent"]}% popusta na prvu narudžbu.',
                    )
                elif reg_reward:
                    messages.success(
                        request,
                        'Dobrodošli! Nalog je spreman — besplatna dostava na prvu narudžbu.',
                    )
                else:
                    messages.success(
                        request,
                        'Dobrodošli! Nalog je kreiran i odmah ste prijavljeni.',
                    )
                next_url = request.GET.get('next') or request.POST.get('next') or '/'
                if not str(next_url).startswith('/'):
                    next_url = '/'
                return redirect(next_url)

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
                from .live_visitor_offer import get_active_registration_reward_coupon
                reg_coupon = get_active_registration_reward_coupon(form.user)
                play_gift_after = False
                try:
                    from .online_gift import should_play_gift_after_auth
                    play_gift_after = should_play_gift_after_auth(request)
                except Exception:
                    play_gift_after = False
                if reg_coupon:
                    pct = reg_coupon.postotak
                    pct_label = int(pct) if pct == int(pct) else pct
                    messages.success(
                        request,
                        f'Uspješno ste se prijavili. Imate {pct_label}% popusta '
                        f'na prvu narudžbu — automatski se primjenjuje u korpi.',
                    )
                elif play_gift_after:
                    messages.success(
                        request,
                        'Uspješno ste se prijavili — sada možete odigrati nagradnu igru!',
                    )
                else:
                    messages.success(request, 'Uspješno ste se prijavili.')
                redirect_to = request.POST.get('next') or next_url
                # Poslije nagrade-registracije vodi na početnu da se popup odmah prikaže
                if play_gift_after and (not redirect_to or redirect_to.startswith('/nalog')):
                    redirect_to = '/'
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
    loyalty_card = osiguraj_loyalty_karticu(request.user)
    loyalty = loyalty_kontekst(loyalty_card)
    cardholder_name = (
        request.user.get_full_name().strip()
        or request.user.first_name
        or (request.user.email or '').strip().lower()
    )

    context = {
        **_base_context(),
        'profile_form': profile_form,
        'orders': orders,
        'loyalty': loyalty,
        'cardholder_name': cardholder_name,
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
        'stavke': pripremi_stavke_za_racun(order),
        'pricing': order.pdv_pregled,
    }
    return render(request, 'account/order_detail.html', context)


def _superuser_required(user):
    return user.is_authenticated and user.is_superuser


def _staff_required(user):
    """Staff ili superuser — npr. Admin panel ulaz i Loyalty System."""
    return user.is_authenticated and (user.is_staff or user.is_superuser)


def _staff_upload_is_image(uploaded_file):
    content_type = getattr(uploaded_file, 'content_type', '') or ''
    return content_type.startswith('image/')


def _staff_parse_tag_ids(request):
    tag_ids = []
    for raw in request.POST.getlist('tag_ids'):
        try:
            tag_id = int(raw)
        except (TypeError, ValueError):
            continue
        if tag_id not in tag_ids:
            tag_ids.append(tag_id)
    return tag_ids


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


def _mark_order_completed(request, broj):
    order = get_object_or_404(Order, broj=broj)
    if order.status == Order.Status.NOVA:
        order.status = Order.Status.ZAVRSENA
        order.save(update_fields=['status'])
        messages.success(request, f'Narudžba #{broj} označena kao završena.')
        return True
    messages.info(request, f'Narudžba #{broj} više nije nova.')
    return False


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_lookup(request):
    query = request.GET.get('q', '').strip()
    url = reverse('staff_online_orders')
    if query:
        url = f'{url}?{urlencode({"q": query})}'
    return redirect(url)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_detail(request, broj):
    order = get_object_or_404(
        Order.objects.prefetch_related('stavke'),
        broj=broj,
    )

    if request.method == 'POST' and request.POST.get('action') == 'zavrsi':
        _mark_order_completed(request, broj)
        return redirect('staff_online_orders')

    context = {
        **_base_context(),
        **get_order_email_context(order),
    }
    return render(request, 'staff/order_detail.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_toggle_edit_mode(request):
    """Uključi/isključi edit mode na sajtu (superuser)."""
    raw = (request.POST.get('enabled') or request.POST.get('edit_mode') or '').strip().lower()
    if raw in ('1', 'true', 'on', 'yes', 'da'):
        enabled = True
    elif raw in ('0', 'false', 'off', 'no', 'ne'):
        enabled = False
    else:
        # toggle
        enabled = not _staff_edit_mode_enabled(request)
    request.session[STAFF_EDIT_MODE_SESSION_KEY] = enabled
    request.session.modified = True
    if enabled:
        messages.success(
            request,
            'Edit mode uključen — klikni natpise da ih mijenjaš, boje u panelu desno.',
        )
    else:
        messages.success(request, 'Edit mode isključen — artikli se prikazuju kao običnom korisniku.')
    next_url = (request.POST.get('next') or request.META.get('HTTP_REFERER') or '').strip()
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('account')


# Polja SiteSettings koja se smiju mijenjati s fronta u edit modu
_SITE_EDIT_TEXT_FIELDS = frozenset({
    'naslov_novo', 'podnaslov_novo',
    'naslov_izdvojeno', 'podnaslov_izdvojeno',
    'naslov_blog',
    'naslov_povezani', 'podnaslov_povezani',
    'promo_bar_tekst', 'promo_bar_link_tekst',
    'dostava_naziv',
    'tekst_dugme_korpa', 'tekst_dugme_rasprodato',
})
_SITE_EDIT_COLOR_FIELDS = frozenset({
    'boja_dugme_korpa', 'boja_dugme_korpa_hover',
    'boja_dugme_banner', 'boja_dugme_banner_hover',
    'kontakt_boja_whatsapp', 'kontakt_boja_viber', 'kontakt_boja_messenger',
})
_SITE_EDIT_MAX_LEN = {
    'naslov_novo': 120,
    'podnaslov_novo': 200,
    'naslov_izdvojeno': 120,
    'podnaslov_izdvojeno': 200,
    'naslov_blog': 200,
    'naslov_povezani': 120,
    'podnaslov_povezani': 200,
    'promo_bar_tekst': 200,
    'promo_bar_link_tekst': 80,
    'dostava_naziv': 100,
    'tekst_dugme_korpa': 40,
    'tekst_dugme_rasprodato': 40,
}


def _site_edit_normalize_value(field, value):
    import re
    value = str(value if value is not None else '').strip()
    if field in _SITE_EDIT_TEXT_FIELDS:
        max_len = _SITE_EDIT_MAX_LEN.get(field, 200)
        return value[:max_len], None
    if field in _SITE_EDIT_COLOR_FIELDS:
        if not re.fullmatch(r'#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?', value):
            return None, 'Boja mora biti hex npr. #5BB805.'
        if len(value) == 4:
            value = '#' + ''.join(c * 2 for c in value[1:])
        return value, None
    return None, f'Polje „{field}” nije dozvoljeno.'


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_site_edit_save(request):
    """
    AJAX: snimi jedno ili više polja SiteSettings (edit mode).
    Single: field + value
    Multi: updates_json = {"boja_dugme_korpa":"#…","tekst_dugme_korpa":"…"}
    """
    import json
    from django.http import JsonResponse

    if not _staff_edit_mode_enabled(request):
        return JsonResponse({'ok': False, 'message': 'Edit mode je isključen.'}, status=403)

    updates = {}
    raw_json = (request.POST.get('updates_json') or '').strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return JsonResponse({'ok': False, 'message': 'Neispravan JSON.'}, status=400)
        if not isinstance(parsed, dict) or not parsed:
            return JsonResponse({'ok': False, 'message': 'Prazan updates.'}, status=400)
        updates = parsed
    else:
        field = (request.POST.get('field') or '').strip()
        value = request.POST.get('value')
        if not field:
            return JsonResponse({'ok': False, 'message': 'Nedostaje field.'}, status=400)
        updates = {field: value}

    site = SiteSettings.load()
    saved = {}
    color_changed = False
    for field, raw_val in updates.items():
        field = str(field).strip()
        value, err = _site_edit_normalize_value(field, raw_val)
        if err:
            return JsonResponse({'ok': False, 'message': err}, status=400)
        if not hasattr(site, field):
            return JsonResponse({'ok': False, 'message': f'Nepoznato polje „{field}”.'}, status=400)
        setattr(site, field, value)
        saved[field] = value
        if field in _SITE_EDIT_COLOR_FIELDS:
            color_changed = True

    site.save(update_fields=list(saved.keys()))
    theme_css = site.get_theme_ui().get('css_vars', '') if color_changed else ''

    return JsonResponse({
        'ok': True,
        'saved': saved,
        'theme_css': theme_css,
        'message': 'Sačuvano.',
    })


@login_required(login_url='login')
@user_passes_test(_staff_required)
def staff_admin_panel(request):
    from .models import Order

    nova_count = 0
    if request.user.is_superuser:
        nova_count = Order.objects.filter(status=Order.Status.NOVA).count()
    context = {
        **_base_context(),
        'olx_chat_configured': olx_chat_configured(),
        'is_superuser_staff': request.user.is_superuser,
        'new_orders_count': nova_count,
    }
    return render(request, 'staff/admin_panel.html', context)


@login_required(login_url='login')
@user_passes_test(_staff_required)
@require_POST
def staff_activate_user(request):
    """Ručna aktivacija naloga (email aktivacija nije završena)."""
    from django.contrib.auth.models import User

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        user_id = int(request.POST.get('user_id') or 0)
    except (TypeError, ValueError):
        user_id = 0

    target = User.objects.filter(
        pk=user_id,
        is_superuser=False,
        is_staff=False,
    ).first()
    if not target:
        msg = 'Kupac nije pronađen.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': msg}, status=404)
        messages.error(request, msg)
        return redirect('staff_admin_panel')

    if target.is_active:
        msg = f'Nalog {target.email or target.username} je već aktivan.'
        if is_ajax:
            return JsonResponse({
                'ok': True,
                'message': msg,
                'already_active': True,
                'user_id': target.pk,
                'is_active': True,
            })
        messages.info(request, msg)
    else:
        target.is_active = True
        target.save(update_fields=['is_active'])
        msg = f'Nalog {target.email or target.username} je aktiviran. Kupac se sada može prijaviti.'
        if is_ajax:
            return JsonResponse({
                'ok': True,
                'message': msg,
                'already_active': False,
                'user_id': target.pk,
                'is_active': True,
            })
        messages.success(request, msg)

    next_url = (request.POST.get('next') or '').strip()
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    q = (request.POST.get('q') or '').strip()
    if q:
        return redirect(f"{reverse('staff_loyalty_system')}?{urlencode({'q': q})}")
    return redirect('staff_loyalty_system')


def _live_analytics_context(request):
    from .live_visitors import (
        get_live_visitor_snapshot,
        get_registered_customers,
        get_visitor_traffic_stats,
        parse_traffic_filters,
    )
    from .online_gift import get_online_gift_staff_feed

    snapshot = get_live_visitor_snapshot()
    traffic_filters = parse_traffic_filters(request)
    traffic_stats = get_visitor_traffic_stats(
        daily_from=traffic_filters['daily_from_date'],
        daily_to=traffic_filters['daily_to_date'],
        monthly_from=traffic_filters['monthly_from_date'],
        monthly_to=traffic_filters['monthly_to_date'],
    )
    online_user_ids = {
        row.get('user_id')
        for row in (snapshot.get('registered_online_visitors') or [])
        if row.get('user_id')
    }
    registered_customers = get_registered_customers(online_user_ids=online_user_ids)
    gift_feed = get_online_gift_staff_feed()
    from .online_gift import get_campaign_staff_status
    gift_campaign = get_campaign_staff_status()
    generated_at = snapshot['generated_at']
    online_visitors = snapshot['online_visitors'] or []
    window_visitors = snapshot['window_visitors'] or []
    offline_visitors = [row for row in window_visitors if not row.get('is_online')]
    return {
        'online_count': snapshot['online_count'],
        'window_count': snapshot['window_count'],
        'offline_count': len(offline_visitors),
        'registered_online_count': snapshot.get('registered_online_count', 0),
        'registered_window_count': snapshot.get('registered_window_count', 0),
        'registered_customers_count': len(registered_customers),
        'registered_customers': registered_customers,
        'online_visitors': online_visitors,
        'window_visitors': window_visitors,
        'offline_visitors': offline_visitors,
        'registered_online_visitors': snapshot.get('registered_online_visitors') or [],
        'registered_window_visitors': snapshot.get('registered_window_visitors') or [],
        'online_minutes': snapshot['online_minutes'],
        'window_minutes': snapshot['window_minutes'],
        'daily_stats': traffic_stats['daily'],
        'monthly_stats': traffic_stats['monthly'],
        'city_stats': traffic_stats['by_city'],
        'city_stats_json': traffic_stats['by_city'],
        'traffic_filters': traffic_filters,
        'gift_winners': gift_feed.get('winners') or [],
        'gift_winners_count': gift_feed.get('winners_count') or 0,
        'gift_ordered_count': gift_feed.get('ordered_count') or 0,
        'gift_online_winners_count': gift_feed.get('online_winners_count') or 0,
        'gift_feed_hours': gift_feed.get('hours') or 48,
        'gift_campaign': gift_campaign,
        'generated_at': generated_at,
        'generated_at_label': generated_at.strftime('%H:%M:%S'),
    }


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_live_analytics(request):
    context = {
        **_base_context(),
        **_live_analytics_context(request),
    }
    return render(request, 'staff/live_analytics.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def staff_product_search(request):
    query = request.GET.get('q', '').strip()
    products_qs = Product.objects.filter(aktivan=True)
    if query:
        products_qs = products_qs.filter(
            Q(naziv__icontains=query)
            | Q(sifra__icontains=query)
            | Q(slug__icontains=query),
        )
    products = list(products_qs.order_by('naziv')[:STAFF_LOOKUP_LIMIT])
    results = []
    for product in products:
        price = _effective_product_price(product)
        results.append({
            'id': product.pk,
            'label': product.naziv,
            'sifra': product.sifra or '',
            'price': f'{price:.2f}',
            'image': product.prikazna_slika.url if product.prikazna_slika else '',
        })
    return JsonResponse({'results': results, 'query': query})


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_send_live_offer(request):
    from django.contrib.auth.models import User

    from .live_visitor_offer import send_live_visitor_offer
    from .models import LiveVisitor, LiveVisitorOffer

    session_key = (request.POST.get('session_key') or '').strip()
    email_to = (request.POST.get('email') or '').strip()
    try:
        user_id = int(request.POST.get('user_id') or 0)
    except (TypeError, ValueError):
        user_id = 0
    try:
        product_id = int(request.POST.get('product_id') or 0)
    except (TypeError, ValueError):
        product_id = 0
    # Više artikala odjednom: product_ids=1,2,3 (svi pregledani)
    product_ids = []
    raw_ids = (request.POST.get('product_ids') or '').strip()
    if raw_ids:
        for part in raw_ids.replace(';', ',').split(','):
            part = part.strip()
            if not part:
                continue
            try:
                pid = int(part)
            except (TypeError, ValueError):
                continue
            if pid > 0 and pid not in product_ids:
                product_ids.append(pid)
    if product_id and product_id not in product_ids:
        product_ids.insert(0, product_id)
    try:
        discount_percent = Decimal(
            (request.POST.get('discount_percent') or '0').replace(',', '.'),
        )
    except (InvalidOperation, ValueError):
        discount_percent = Decimal('0')
    free_shipping = (request.POST.get('free_shipping') or '').strip().lower() in {
        '1', 'true', 'on', 'yes', 'da',
    }

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not product_ids and discount_percent <= 0 and not free_shipping:
        msg = 'Unesite popust %, besplatnu dostavu ili odaberite artikal.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('staff_live_analytics')

    visitor = None
    target_user = None
    visitor_name = ''

    if session_key:
        visitor = LiveVisitor.objects.filter(session_key=session_key).select_related('user').first()
        if visitor:
            target_user = visitor.user if visitor.user_id else None
            visitor_name = (visitor.ime or '').strip()
            if not email_to:
                email_to = (visitor.email or '').strip()
                if not email_to and visitor.user_id and visitor.user:
                    email_to = (visitor.user.email or '').strip()

    if user_id and not target_user:
        target_user = User.objects.filter(
            pk=user_id, is_active=True, is_superuser=False,
        ).first()
        if target_user:
            visitor_name = (
                target_user.get_full_name().strip()
                or (target_user.first_name or '').strip()
                or (target_user.email or '').split('@', 1)[0]
            )
            if not email_to:
                email_to = (target_user.email or '').strip()
            if not session_key:
                live = (
                    LiveVisitor.objects.filter(user_id=target_user.pk)
                    .order_by('-last_seen')
                    .first()
                )
                if live:
                    session_key = live.session_key
                    visitor = live

    if email_to and not target_user:
        target_user = User.objects.filter(
            email__iexact=email_to, is_active=True, is_superuser=False,
        ).first()
        if target_user and not visitor_name:
            visitor_name = (
                target_user.get_full_name().strip()
                or (target_user.first_name or '').strip()
                or email_to.split('@', 1)[0]
            )
        if target_user and not session_key:
            live = (
                LiveVisitor.objects.filter(user_id=target_user.pk)
                .order_by('-last_seen')
                .first()
            )
            if live:
                session_key = live.session_key
                visitor = live

    # Offline registrovani: koristi stabilan session_key vezan za user/email
    # (popup se veže na user_id pa radi kad se prijave)
    if not session_key:
        if target_user:
            session_key = f'reg-user-{target_user.pk}'
        elif email_to:
            session_key = f'reg-email-{email_to.lower()[:80]}'
        else:
            msg = 'Nema sesije ni emaila kupca.'
            if is_ajax:
                return JsonResponse({'ok': False, 'message': msg}, status=400)
            messages.error(request, msg)
            return redirect('staff_live_analytics')

    email_only = not bool(visitor)

    try:
        offers_sent = []
        skipped = []
        if product_ids:
            for pid in product_ids:
                try:
                    offer = send_live_visitor_offer(
                        session_key,
                        product_id=pid,
                        discount_percent=discount_percent,
                        free_shipping=free_shipping,
                        staff_user=request.user,
                        target_user=target_user,
                    )
                    offers_sent.append(offer)
                except ValueError as exc:
                    skipped.append(str(exc))
            if not offers_sent:
                raise ValueError(skipped[0] if skipped else 'Nijedna ponuda nije poslana.')
            offer = offers_sent[0]
            pct = (
                int(discount_percent)
                if discount_percent == int(discount_percent)
                else discount_percent
            )
            if len(offers_sent) == 1:
                success_message = (
                    f'Ponuda artikla poslana kupcu'
                    + (f' s popustom {pct}%.' if discount_percent > 0 else '.')
                )
            else:
                success_message = (
                    f'Poslano {len(offers_sent)} ponuda na pregledane artikle'
                    + (f' s -{pct}%.' if discount_percent > 0 else '.')
                )
                if skipped:
                    success_message += f' ({len(skipped)} preskočeno — već prihvaćeno).'
        else:
            offer = send_live_visitor_offer(
                session_key,
                product_id=None,
                discount_percent=discount_percent,
                free_shipping=free_shipping,
                staff_user=request.user,
                target_user=target_user,
            )
            offers_sent = [offer]
            extras = []
            if free_shipping:
                extras.append('besplatna dostava na prvu kupovinu')
            if offer.tip == LiveVisitorOffer.Tip.NARUDZBA:
                if discount_percent > 0:
                    pct = int(discount_percent) if discount_percent == int(discount_percent) else discount_percent
                    success_message = (
                        f'Kod za {pct}% popusta na narudžbu poslan kupcu ({offer.aktivacioni_kod}).'
                    )
                else:
                    success_message = (
                        f'Ponuda besplatne dostave poslana kupcu ({offer.aktivacioni_kod}).'
                    )
            else:
                success_message = 'Ponuda poslana kupcu.'
            if free_shipping and 'besplatna dostava' not in success_message.lower():
                success_message = f'{success_message} + {extras[0]}.'

        if email_to:
            try:
                from .emails import send_live_offer_email
                # Email za prvu (ili jedinu) ponudu
                send_live_offer_email(
                    to_email=email_to,
                    visitor_name=visitor_name or '',
                    offer=offer,
                )
                success_message = f'{success_message} Email poslan na {email_to}.'
            except Exception:
                if email_only:
                    raise ValueError('Slanje emaila nije uspjelo. Provjerite email postavke.')
                success_message = (
                    f'{success_message} Popup je aktivan, ali slanje emaila nije uspjelo.'
                )
        elif email_only:
            raise ValueError('Kupac nema email adresu.')

        if is_ajax:
            return JsonResponse({
                'ok': True,
                'message': success_message,
                'offers_count': len(offers_sent),
            })
        messages.success(request, success_message)
    except ValueError as exc:
        if is_ajax:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
        messages.error(request, str(exc))
    return redirect('staff_live_analytics')


@user_passes_test(_superuser_required)
@require_POST
def staff_send_registration_invite(request):
    from .live_visitor_offer import send_live_visitor_registration_invite
    from .models import LiveVisitor

    session_key = (request.POST.get('session_key') or '').strip()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    visitor = LiveVisitor.objects.filter(session_key=session_key).select_related('user').first()
    if not session_key or not visitor:
        msg = 'Posjetilac nije pronađen.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('staff_live_analytics')
    if visitor.user_id:
        msg = 'Kupac je već registrovan.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('staff_live_analytics')

    try:
        send_live_visitor_registration_invite(
            session_key,
            staff_user=request.user,
        )
        success_message = (
            'Poziv na registraciju poslan kupcu '
            '(besplatna dostava na prvu narudžbu).'
        )
        if is_ajax:
            return JsonResponse({'ok': True, 'message': success_message})
        messages.success(request, success_message)
    except ValueError as exc:
        if is_ajax:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
        messages.error(request, str(exc))
    return redirect('staff_live_analytics')


@require_POST
def live_visitor_offer_add(request):
    from .live_visitor_offer import apply_live_visitor_offer

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    stay_on_page = request.POST.get('stay') == '1' or is_ajax
    try:
        cart = Cart(request)
        ok, result = apply_live_visitor_offer(request, cart)
    except Exception:
        if stay_on_page:
            return JsonResponse(
                {'ok': False, 'message': 'Dodavanje u korpu nije uspjelo.'},
                status=500,
            )
        raise
    if stay_on_page:
        if ok:
            return JsonResponse({
                'ok': True,
                'message': result,
                'cart_count': len(cart),
            })
        return JsonResponse({'ok': False, 'message': result}, status=400)
    if ok:
        messages.success(request, result)
    else:
        messages.warning(request, result)
    return redirect('cart')


@require_POST
def live_visitor_offer_activate(request):
    from .live_visitor_offer import activate_live_visitor_offer_code

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    cart = Cart(request)
    ok, result = activate_live_visitor_offer_code(request, cart)
    if is_ajax:
        if ok:
            return JsonResponse({
                'ok': True,
                'message': result['message'],
                'percent': result['percent'],
            })
        return JsonResponse({'ok': False, 'message': result}, status=400)
    if ok:
        messages.success(request, result['message'])
    else:
        messages.warning(request, result)
    return redirect('home')


@require_POST
def live_visitor_offer_dismiss(request):
    from .live_visitor_offer import dismiss_live_visitor_offer

    dismiss_live_visitor_offer(request)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('home')
    return redirect(next_url)


@require_POST
def browse_interest_offer_add(request):
    from .browse_interest_offer import apply_browse_interest_offer

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    stay_on_page = request.POST.get('stay') == '1' or is_ajax
    try:
        cart = Cart(request)
        ok, result = apply_browse_interest_offer(request, cart)
    except Exception:
        if stay_on_page:
            return JsonResponse(
                {'ok': False, 'message': 'Dodavanje u korpu nije uspjelo.'},
                status=500,
            )
        raise
    if stay_on_page:
        if ok:
            return JsonResponse({
                'ok': True,
                'message': result,
                'cart_count': len(cart),
            })
        return JsonResponse({'ok': False, 'message': result}, status=400)
    if ok:
        messages.success(request, result)
    else:
        messages.warning(request, result)
    return redirect('cart')


@require_POST
def ai_dwell_activate(request):
    """Aktiviraj flash cijenu odmah na ulasku na artikal (bez popupa)."""
    from .live_visitor_offer import activate_product_dwell_flash

    try:
        product_id = int(request.POST.get('product_id') or 0)
    except (TypeError, ValueError):
        product_id = 0
    # force samo staff + eksplicitni flag (ne obnavlja se na običan refresh)
    force = False
    if request.POST.get('force') == '1':
        u = getattr(request, 'user', None)
        force = bool(
            u
            and getattr(u, 'is_authenticated', False)
            and (getattr(u, 'is_staff', False) or getattr(u, 'is_superuser', False))
        )
    flash, err = activate_product_dwell_flash(request, product_id, force=force)
    if not flash:
        return JsonResponse({'ok': False, 'message': err or 'Nije aktivirano.'}, status=400)
    pct = flash.get('percent')
    try:
        pct_f = float(pct)
        pct_out = int(pct_f) if pct_f == int(pct_f) else pct_f
    except (TypeError, ValueError):
        pct_out = str(pct)
    return JsonResponse({
        'ok': True,
        'product_id': flash['product_id'],
        'percent': pct_out,
        'remaining_seconds': flash['remaining_seconds'],
        'expires_ts': flash['expires_ts'],
        'base': flash.get('base'),
        'sale': flash.get('sale'),
    })


@require_http_methods(['GET', 'POST'])
def fishing_advisor_step(request):
    """Virtuelni ribolovački savjetnik — vođeni chat (svi kupci)."""
    from .models import SiteSettings

    try:
        if not SiteSettings.load().savjetnik_aktivan:
            return JsonResponse({
                'ok': False,
                'disabled': True,
                'messages': [{'role': 'bot', 'text': 'Savjetnik trenutno nije aktivan.'}],
                'options': [],
                'state': {},
                'step': 'start',
            }, status=503)
    except Exception:
        pass

    from .fishing_advisor import process_step

    if request.method == 'GET':
        data = process_step('start', '', {}, request=request)
        return JsonResponse(data)

    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        body = {}
    if not body:
        body = {
            'step': request.POST.get('step') or 'start',
            'answer': request.POST.get('answer') or '',
        }
        state_raw = request.POST.get('state')
        if state_raw:
            try:
                body['state'] = json.loads(state_raw)
            except json.JSONDecodeError:
                body['state'] = {}

    step = body.get('step') or 'start'
    answer = body.get('answer') or ''
    state = body.get('state') if isinstance(body.get('state'), dict) else {}
    data = process_step(step, answer, state, request=request)
    return JsonResponse(data)


@require_POST
def fishing_advisor_buy_set(request):
    """Dodaj cijeli početnički set u korpu (opcionalni % popust na set)."""
    from decimal import Decimal, ROUND_HALF_UP

    from .cart import Cart
    from .fishing_advisor import track_advisor_live
    from .models import AdvisorBeginnerSet

    try:
        set_id = int(request.POST.get('set_id') or 0)
    except (TypeError, ValueError):
        set_id = 0
    kit = (
        AdvisorBeginnerSet.objects
        .filter(pk=set_id, aktivan=True, fish_type__aktivan=True)
        .prefetch_related('stavke__product')
        .first()
    )
    if not kit:
        return JsonResponse({'ok': False, 'message': 'Set nije pronađen.'}, status=404)

    stavke = [
        s for s in kit.stavke.all()
        if s.product_id
        and getattr(s.product, 'aktivan', False)
        and getattr(s.product, 'na_stanju', False)
    ]
    # Izbaci štap/mašinicu ako kupac već ima (isti filter kao u savjetniku)
    from .fishing_advisor import _filter_stavke_by_owned
    owned = (request.POST.get('owned') or '').strip().lower()
    if not owned:
        try:
            from .cart_tracking import get_cart_session_key
            from .models import LiveVisitor
            sk = get_cart_session_key(request)
            lv = LiveVisitor.objects.filter(session_key=sk).only('savjetnik').first()
            if lv and isinstance(lv.savjetnik, dict):
                # zadnji odgovor owned iz answers ili polje
                owned = (lv.savjetnik.get('owned') or '')[:40]
                if not owned:
                    for a in reversed(lv.savjetnik.get('answers') or []):
                        if a.get('step') == 'owned':
                            owned = (a.get('answer_id') or '')[:40]
                            break
        except Exception:
            owned = ''
    stavke = _filter_stavke_by_owned(stavke, owned)
    if not stavke:
        return JsonResponse({
            'ok': False,
            'message': 'U setu nema preostalih artikala (već imaš tu opremu).',
        }, status=400)

    cart = Cart(request)
    pct = kit.popust_postotak
    has_disc = bool(pct and pct > 0)
    added = 0
    for item in stavke:
        product = item.product
        qty = max(1, int(item.kolicina or 1))
        unit = product.prikazna_cijena
        custom = None
        promo_bazna = None
        if has_disc:
            try:
                faktor = Decimal('1') - (Decimal(pct) / Decimal('100'))
                custom = (Decimal(str(unit)) * faktor).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP,
                )
                promo_bazna = unit
            except Exception:
                custom = None
        set_src = f'Savjetnik set „{kit.naziv}”'
        if has_disc:
            set_src = f'{set_src} (−{pct}%)'
        cart.add(
            product,
            quantity=qty,
            custom_price=custom,
            promo_bazna=promo_bazna,
            discount_source=set_src if has_disc else None,
            discount_percent=pct if has_disc else None,
        )
        added += qty

    from .cart_tracking import sync_active_cart
    try:
        sync_active_cart(request)
    except Exception:
        pass

    label = kit.naziv
    try:
        track_advisor_live(
            request,
            step='results',
            answer='buy_set',
            state={'owned': owned},
            accepted_set=label,
        )
    except Exception:
        pass

    skip_note = ''
    if owned == 'masinica':
        skip_note = ' (bez mašinice)'
    elif owned == 'stap':
        skip_note = ' (bez štapa)'
    elif owned == 'skoro_sve':
        skip_note = ' (bez štapa/mašinice)'
    if has_disc:
        msg = f'Set „{label}” dodan u korpu (−{pct}%){skip_note}.'
    else:
        msg = f'Set „{label}” dodan u korpu{skip_note}.'
    return JsonResponse({
        'ok': True,
        'message': msg,
        'cart_count': len(cart),
        'added_lines': len(stavke),
        'added_qty': added,
    })


@require_POST
def browse_interest_offer_dismiss(request):
    from .browse_interest_offer import dismiss_browse_interest_offer

    dismiss_browse_interest_offer(request)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('home')
    return redirect(next_url)


@require_GET
def social_proof_poll(request):
    """JSON za toast „neko je kupio…” (svaka 3 min na frontu)."""
    from .social_proof import build_social_proof_payload, _should_show_social_proof

    if not _should_show_social_proof(request):
        return JsonResponse({'active': False})

    exclude = []
    raw = (request.GET.get('exclude') or '').strip()
    if raw:
        for part in raw.split(','):
            try:
                exclude.append(int(part.strip()))
            except (TypeError, ValueError):
                continue

    proof = build_social_proof_payload(request, exclude_ids=exclude)
    if not proof:
        return JsonResponse({'active': False})
    return JsonResponse({'active': True, 'proof': proof})


@require_POST
def online_gift_reveal(request):
    from .online_gift import reveal_online_gift

    try:
        result = reveal_online_gift(request)
        return JsonResponse(result)
    except ValueError as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
    except Exception:
        logger.exception('online_gift_reveal')
        return JsonResponse(
            {'ok': False, 'message': 'Nagrada nije uspjela. Pokušajte ponovo.'},
            status=500,
        )


@require_POST
def online_gift_dismiss(request):
    from .online_gift import dismiss_online_gift

    dismiss_online_gift(request)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse('home')
    return redirect(next_url)


@ensure_csrf_cookie
@require_GET
def online_gift_poll(request):
    """Poll — staff ručno pušta nagradu dok je kupac na sajtu."""
    from .online_gift import poll_online_gift

    if request.user.is_authenticated and request.user.is_superuser:
        payload = {'active': False}
    else:
        payload = poll_online_gift(request)
    payload['csrf_token'] = get_token(request)
    return JsonResponse(payload)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_push_online_gift(request):
    """Ručno pusti online nagradu odabranom live kupcu."""
    from .models import LiveVisitor
    from .online_gift import push_online_gift_to_visitor

    session_key = (request.POST.get('session_key') or '').strip()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    visitor = (
        LiveVisitor.objects.filter(session_key=session_key)
        .select_related('user')
        .first()
    )
    if not session_key or not visitor:
        msg = 'Posjetilac nije pronađen.'
        if is_ajax:
            return JsonResponse({'ok': False, 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect('staff_live_analytics')

    try:
        push, created = push_online_gift_to_visitor(
            session_key=session_key,
            staff_user=request.user,
            target_user=visitor.user if visitor.user_id else None,
        )
        name = (visitor.ime or '').strip() or 'kupcu'
        success_message = (
            f'Online nagrada puštena za {name}. '
            f'Popup će se pojaviti na njihovom ekranu za nekoliko sekundi.'
        )
        if not created:
            success_message = (
                f'Online nagrada ponovo puštena za {name}.'
            )
        if is_ajax:
            return JsonResponse({
                'ok': True,
                'message': success_message,
                'push_id': push.pk,
            })
        messages.success(request, success_message)
    except ValueError as exc:
        if is_ajax:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
        messages.error(request, str(exc))
    return redirect('staff_live_analytics')


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_set_online_gift_automatic(request):
    """Uključi/isključi automatski režim online nagrade (uživo analitika)."""
    from .online_gift import get_campaign_staff_status, set_campaign_automatic

    raw = (request.POST.get('automatic') or '').strip().lower()
    automatic = raw in {'1', 'true', 'on', 'yes', 'da'}
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    try:
        campaign = set_campaign_automatic(automatic)
        if campaign.automatic:
            msg = (
                f'Automatski režim UKLJUČEN — na stranicama iskače ponuda nagradne igre; '
                f'kupac sam bira „Da, igraj” ili „Ne, hvala” (jednom po posjetiocu). '
                f'Nagrada: {campaign.prize_label()}.'
            )
        else:
            msg = (
                'Automatski režim ISKLJUČEN — nagrada se ne pojavljuje sama. '
                'Pusti je ručno pored kupca (🎁 Nagrada).'
            )
        status = get_campaign_staff_status()
        if is_ajax:
            return JsonResponse({'ok': True, 'message': msg, **status})
        messages.success(request, msg)
    except ValueError as exc:
        if is_ajax:
            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
        messages.error(request, str(exc))
    return redirect('staff_live_analytics')


@ensure_csrf_cookie
@require_GET
def live_visitor_offer_poll(request):
    from .live_visitor_offer import poll_live_visitor_offer

    if request.user.is_authenticated and request.user.is_superuser:
        payload = {'active': False}
    else:
        offer = poll_live_visitor_offer(request)
        if not offer:
            payload = {'active': False}
        else:
            payload = {'active': True, 'offer': offer}

    payload['csrf_token'] = get_token(request)
    return JsonResponse(payload)


@require_POST
def almost_cart_track(request):
    """
    Kursor na „Dodaj u korpu” bez klika → skoro_korpa.
    clicked=1 briše (korisnik je kliknuo).
    """
    from .almost_cart import record_almost_cart

    try:
        product_id = int(request.POST.get('product_id') or 0)
    except (TypeError, ValueError):
        product_id = 0
    if not product_id:
        return JsonResponse({'ok': False, 'message': 'Nedostaje artikal.'}, status=400)

    product_name = (request.POST.get('product_name') or '')[:120]
    clicked = (request.POST.get('clicked') or '') in ('1', 'true', 'yes')
    record_almost_cart(
        request,
        product_id,
        product_name=product_name,
        clicked=clicked,
    )
    return JsonResponse({'ok': True, 'clicked': clicked})


@csrf_exempt
@require_POST
def live_visitor_heartbeat(request):
    """Ping dok je posjetilac na sajtu (osvježava last_seen + presence)."""
    from .live_visitors import heartbeat_live_visitor

    body_key = (
        request.POST.get('session_key')
        or request.GET.get('session_key')
        or ''
    )
    if request.user.is_authenticated and request.user.is_superuser:
        return JsonResponse({'ok': True, 'tracked': False})
    tracked = heartbeat_live_visitor(request, body_session_key=body_key)
    return JsonResponse({'ok': True, 'tracked': tracked})


@require_GET
def public_online_visitors(request):
    """
    Javni API: ko je trenutno na sajtu (samo ako je uključeno u Podešavanjima).
    Privatno — bez emaila i punog imena.
    """
    from .models import SiteSettings
    from .live_visitors import public_online_visitors_payload

    try:
        enabled = bool(SiteSettings.load().javno_online_posjetioci)
    except Exception:
        enabled = False
    if not enabled:
        return JsonResponse({'ok': False, 'disabled': True, 'count': 0, 'items': []}, status=404)
    payload = public_online_visitors_payload(limit=24)
    response = JsonResponse(payload)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@csrf_exempt
@require_POST
def live_visitor_leave(request):
    """
    Beacon kad posjetilac zatvori tab / ode sa sajta.
    csrf_exempt: sendBeacon ne šalje pouzdano CSRF; veže se na sesiju (+ session_key u body).
    """
    from .live_visitors import mark_live_visitor_left

    body_key = (
        request.POST.get('session_key')
        or request.GET.get('session_key')
        or ''
    )
    # sendBeacon body nije uvijek u request.POST — parsiraj raw body
    if not body_key and request.body:
        try:
            from urllib.parse import parse_qs
            parsed = parse_qs(request.body.decode('utf-8', errors='ignore'))
            vals = parsed.get('session_key') or []
            if vals:
                body_key = vals[0]
        except Exception:
            body_key = ''
    # FormData leave_at ostaje u request.POST; query fallback
    if not request.POST.get('leave_at') and request.GET.get('leave_at'):
        # mark_live_visitor_left čita i GET
        pass

    if request.user.is_authenticated and request.user.is_superuser:
        return JsonResponse({'ok': True, 'left': False})
    left = mark_live_visitor_left(request, body_session_key=body_key)
    return JsonResponse({'ok': True, 'left': left})


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def staff_live_analytics_data(request):
    from django.utils import timezone

    payload = _live_analytics_context(request)
    payload['generated_at'] = timezone.localtime(payload['generated_at']).isoformat()
    for key in (
        'online_visitors',
        'window_visitors',
        'offline_visitors',
        'registered_online_visitors',
        'registered_window_visitors',
    ):
        for row in payload.get(key) or []:
            if row.get('last_seen') and hasattr(row['last_seen'], 'isoformat'):
                row['last_seen'] = timezone.localtime(row['last_seen']).isoformat()
    for row in payload.get('gift_winners') or []:
        if row.get('won_at') and hasattr(row['won_at'], 'isoformat'):
            row['won_at'] = timezone.localtime(row['won_at']).isoformat()
    # Cache-bust headers — staff live poll mora uvijek dobiti svježe stanje
    response = JsonResponse(payload)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    return response


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def staff_site_events_poll(request):
    """Polling endpoint — live toast obavijesti za superusere na sajtu."""
    from .staff_alerts import get_staff_events_since

    since = request.GET.get('since') or request.GET.get('after') or '0'
    data = get_staff_events_since(since)
    return JsonResponse({
        'ok': True,
        'events': data['events'],
        'latest_id': data['latest_id'],
        'online_sessions': data.get('online_sessions') or [],
        'visitor_states': data.get('visitor_states') or {},
        'new_orders_count': int(data.get('new_orders_count') or 0),
    })


def _active_cart_groups(queryset):
    from collections import defaultdict
    from decimal import Decimal

    buckets = defaultdict(list)
    for item in queryset:
        buckets[item.session_key].append(item)

    groups = []
    for session_key, items in buckets.items():
        items.sort(key=lambda row: row.azurirano, reverse=True)
        user = next((row.user for row in items if row.user_id), None)
        groups.append({
            'session_key': session_key,
            'session_short': session_key[:8] if session_key else '—',
            'user': user,
            'user_email': user.email if user else None,
            'azurirano': max(row.azurirano for row in items),
            'dodano': min(row.dodano for row in items),
            'items': items,
            'ukupno': sum((row.ukupno for row in items), Decimal('0')),
            'stavki': sum(row.kolicina for row in items),
        })
    groups.sort(key=lambda group: group['azurirano'], reverse=True)
    return groups


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_active_carts(request):
    from .cart_recovery import send_cart_recovery_alert
    from .cart_tracking import cleanup_stale_active_cart_items
    cleanup_stale_active_cart_items()

    if request.method == 'POST' and request.POST.get('action') == 'warn':
        session_key = (request.POST.get('session_key') or '').strip()
        try:
            discount_percent = Decimal(
                (request.POST.get('discount_percent') or '0').replace(',', '.'),
            )
        except (InvalidOperation, ValueError):
            discount_percent = Decimal('0')
        cart_item = (
            ActiveCartItem.objects.filter(session_key=session_key)
            .exclude(user__isnull=True)
            .select_related('user')
            .first()
        )
        target_user = cart_item.user if cart_item else None
        try:
            send_cart_recovery_alert(
                session_key,
                discount_percent=discount_percent,
                staff_user=request.user,
                target_user=target_user,
            )
            if discount_percent > 0:
                pct = int(discount_percent) if discount_percent == int(discount_percent) else discount_percent
                messages.success(
                    request,
                    f'Podsjetnik poslan kupcu (sesija {session_key[:8]}…) s popustom {pct}%.',
                )
            else:
                messages.success(request, f'Podsjetnik poslan kupcu (sesija {session_key[:8]}…).')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect('staff_active_carts')

    sort = (request.GET.get('sort') or 'azurirano').strip()
    search_query = (request.GET.get('q') or '').strip()

    qs = ActiveCartItem.objects.select_related('user', 'product', 'variation')
    if search_query:
        qs = qs.filter(
            Q(naziv__icontains=search_query)
            | Q(varijacija_naziv__icontains=search_query)
            | Q(user__email__icontains=search_query)
            | Q(product__naziv__icontains=search_query)
            | Q(product__sifra__icontains=search_query),
        )

    if sort == 'dodano':
        qs = qs.order_by('-dodano', '-azurirano')
    elif sort == 'naziv':
        qs = qs.order_by('naziv', '-azurirano')
    else:
        qs = qs.order_by('-azurirano', '-dodano')

    groups = _active_cart_groups(qs[:1000])
    session_keys = [group['session_key'] for group in groups]
    user_ids = [group['user'].pk for group in groups if group.get('user')]
    alerts = CartRecoveryAlert.objects.filter(
        Q(session_key__in=session_keys) | Q(user_id__in=user_ids),
    )
    alert_by_session = {}
    alert_by_user = {}
    for alert in alerts:
        alert_by_session[alert.session_key] = alert
        if alert.user_id:
            alert_by_user[alert.user_id] = alert
    for group in groups:
        alert = alert_by_session.get(group['session_key'])
        if not alert and group.get('user'):
            alert = alert_by_user.get(group['user'].pk)
        group['recovery_alert'] = alert
        group['recovery_pending'] = bool(alert and alert.show_popup and not alert.discount_applied)
    total_items = sum(len(group['items']) for group in groups)
    context = {
        **_base_context(),
        'cart_groups': groups,
        'cart_group_count': len(groups),
        'cart_item_count': total_items,
        'search_query': search_query,
        'sort': sort,
    }
    return render(request, 'staff/active_carts.html', context)


def _staff_olx_messages_filter(request):
    raw = (request.GET.get('filter') or 'kupci').strip().lower()
    if raw == 'sve':
        return 'sve'
    return 'kupci'


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_olx_messages(request):
    if not olx_chat_configured():
        messages.error(
            request,
            'OLX_API_TOKEN nije postavljen — poruke sa Pik/OLX nisu dostupne.',
        )
        return redirect('staff_admin_panel')

    filter_status = _staff_olx_messages_filter(request)
    customers_only = filter_status != 'sve'
    selected_id = None
    raw_conv = (request.GET.get('conv') or '').strip()
    if raw_conv.isdigit():
        selected_id = int(raw_conv)

    conversations = []
    unread_count = 0
    thread_messages = []
    selected_conversation = None
    olx_error = None

    try:
        inbox = fetch_olx_conversations(customers_only=customers_only)
        conversations = inbox['conversations']
        unread_count = inbox['unread_count']
        if selected_id:
            selected_conversation = next(
                (item for item in conversations if item['id'] == selected_id),
                None,
            )
            listing_url = (selected_conversation or {}).get('listing_url', '')
            thread = fetch_olx_conversation_thread(
                selected_id,
                mark_seen=True,
                listing_url=listing_url,
            )
            thread_messages = thread['messages']
            if selected_conversation and selected_conversation['unread']:
                selected_conversation = {**selected_conversation, 'unread': False}
                conversations = [
                    {**item, 'unread': False} if item['id'] == selected_id else item
                    for item in conversations
                ]
                unread_count = sum(1 for item in conversations if item['unread'])
    except OlxApiError as exc:
        olx_error = str(exc)
        messages.error(request, f'OLX/Pik poruke nisu učitane: {exc}')

    context = {
        **_base_context(),
        'conversations': conversations,
        'thread_messages': thread_messages,
        'selected_conversation': selected_conversation,
        'selected_id': selected_id,
        'filter_status': filter_status,
        'unread_count': unread_count,
        'olx_error': olx_error,
    }
    return render(request, 'staff/olx_messages.html', context)


def _staff_online_orders_filter(request):
    raw = (request.GET.get('filter') or 'nove').strip().lower()
    if raw in ('zavrsene', 'zavrsena'):
        return 'zavrsene'
    if raw == 'sve':
        return 'sve'
    return 'nove'


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_online_orders(request):
    filter_status = _staff_online_orders_filter(request)
    query = (request.GET.get('q') or '').strip()
    searched = bool(query)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        broj = (request.POST.get('broj') or '').strip()
        if action == 'zavrsi' and broj:
            _mark_order_completed(request, broj)
        params = {}
        if filter_status != 'nove':
            params['filter'] = filter_status
        if query:
            params['q'] = query
        redirect_url = reverse('staff_online_orders')
        if params:
            redirect_url = f'{redirect_url}?{urlencode(params)}'
        return redirect(redirect_url)

    if query:
        orders = list(_search_staff_orders(query))
        if len(orders) == 1:
            return redirect('staff_order_detail', broj=orders[0].broj)
    elif filter_status == 'nove':
        orders = list(
            Order.objects.filter(status=Order.Status.NOVA).order_by('-kreirana'),
        )
    elif filter_status == 'zavrsene':
        orders = list(
            Order.objects.filter(status=Order.Status.ZAVRSENA).order_by('-kreirana'),
        )
    else:
        orders = list(
            Order.objects.order_by(
                Case(
                    When(status=Order.Status.NOVA, then=0),
                    default=1,
                ),
                '-kreirana',
            ),
        )

    context = {
        **_base_context(),
        'orders': orders,
        'filter_status': filter_status,
        'search_query': query,
        'searched': searched,
        'nova_count': Order.objects.filter(status=Order.Status.NOVA).count(),
        'zavrsena_count': Order.objects.filter(status=Order.Status.ZAVRSENA).count(),
    }
    return render(request, 'staff/online_orders.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_product_quick_edit(request, slug):
    if not _staff_edit_mode_enabled(request):
        messages.error(request, 'Edit mode je isključen. Uključi ga u Moj nalog ili u headeru.')
        return redirect('product_detail', slug=slug)
    product = get_object_or_404(Product, slug=slug)
    action = (request.POST.get('action') or '').strip()

    if action == 'toggle_stock':
        product.na_stanju = not product.na_stanju
        product.save(update_fields=['na_stanju'])
        status = 'na stanju' if product.na_stanju else 'nije na stanju'
        messages.success(request, f'Artikal „{product.naziv}” sada je {status}.')
    elif action == 'toggle_japan':
        product.proizvedeno_u_japanu = not product.proizvedeno_u_japanu
        product.save(update_fields=['proizvedeno_u_japanu'])
        if product.proizvedeno_u_japanu:
            messages.success(request, f'„{product.naziv}” označen kao Made in Japan.')
        else:
            messages.success(request, f'Made in Japan uklonjen sa „{product.naziv}”.')
    elif action == 'save_all':
        # Jedan Save: brend, kategorija, opis, cijena, glavna slika, noviteti
        changed = []
        errors = []

        raw_price = (request.POST.get('cijena') or '').strip().replace(',', '.')
        if raw_price:
            try:
                new_price = Decimal(raw_price)
                if new_price <= 0:
                    errors.append('Cijena mora biti veća od 0.')
                else:
                    product.cijena = new_price
                    if product.akcija_postotak:
                        product.akcijska_cijena = None
                    changed.append('cijena')
            except (InvalidOperation, ValueError):
                errors.append('Unesite ispravnu cijenu (npr. 45.00).')

        raw_cat = (request.POST.get('kategorija_id') or '').strip()
        if 'kategorija_id' in request.POST:
            if not raw_cat:
                product.kategorija = None
                changed.append('kategorija')
            else:
                try:
                    category = Category.objects.filter(pk=int(raw_cat)).first()
                except (TypeError, ValueError):
                    category = None
                if category:
                    product.kategorija = category
                    changed.append('kategorija')
                else:
                    errors.append('Kategorija nije pronađena.')

        raw_brand = (request.POST.get('brend_id') or '').strip()
        if 'brend_id' in request.POST:
            if not raw_brand:
                product.brend = None
                changed.append('brend')
            else:
                try:
                    brand = Brand.objects.filter(pk=int(raw_brand)).first()
                except (TypeError, ValueError):
                    brand = None
                if brand:
                    product.brend = brand
                    changed.append('brend')
                else:
                    errors.append('Brend nije pronađen.')

        if 'opis' in request.POST:
            product.opis = (request.POST.get('opis') or '').strip()
            changed.append('opis')

        product.je_novitet = (request.POST.get('je_novitet') or '').strip() in (
            '1', 'true', 'on', 'yes',
        )
        changed.append('je_novitet')
        product.je_hit = (request.POST.get('je_hit') or '').strip() in (
            '1', 'true', 'on', 'yes',
        )
        changed.append('je_hit')

        # Pakovanje: checkbox + količina komada (prazno / isključeno = po komadu)
        if 'je_pakovanje' in request.POST or 'pakovanje_komada' in request.POST:
            pack_on = (request.POST.get('je_pakovanje') or '').strip() in (
                '1', 'true', 'on', 'yes',
            )
            raw_pack = (request.POST.get('pakovanje_komada') or '').strip()
            if not pack_on:
                if product.pakovanje_komada:
                    product.pakovanje_komada = None
                    changed.append('pakovanje')
            else:
                try:
                    pack_n = int(raw_pack) if raw_pack else 0
                except (TypeError, ValueError):
                    pack_n = 0
                    errors.append('Pakovanje: unesite cijeli broj komada (npr. 9).')
                if pack_n > 1:
                    if product.pakovanje_komada != pack_n:
                        product.pakovanje_komada = pack_n
                        changed.append('pakovanje')
                elif pack_on and not errors:
                    errors.append('Pakovanje: količina mora biti najmanje 2 komada.')

        uploaded = request.FILES.get('glavna_slika')
        if uploaded:
            if not _staff_upload_is_image(uploaded):
                errors.append('Glavna slika mora biti slika.')
            else:
                product.slika = uploaded
                changed.append('slika')

        if errors:
            for err in errors:
                messages.error(request, err)
            return redirect('product_detail', slug=slug)

        product.save()
        # Dodatne slike (opcionalno u istom save-u)
        uploads = request.FILES.getlist('dodatne_slike')
        extra_n = 0
        if uploads:
            max_order = (
                product.dodatne_slike.aggregate(max_red=Max('redoslijed')).get('max_red') or 0
            )
            for index, up in enumerate(uploads, start=1):
                if not _staff_upload_is_image(up):
                    continue
                ProductImage.objects.create(
                    product=product,
                    slika=up,
                    redoslijed=max_order + index,
                )
                extra_n += 1
        # Tagovi
        if 'tag_ids' in request.POST or request.POST.get('set_tags_with_save'):
            tag_ids = _staff_parse_tag_ids(request)
            tags = list(Tag.objects.filter(pk__in=tag_ids))
            product.tagovi.set(tags)

        parts = []
        if 'cijena' in changed:
            parts.append(f'cijena {product.cijena} KM')
        if 'kategorija' in changed:
            parts.append('kategorija')
        if 'brend' in changed:
            parts.append('brend')
        if 'opis' in changed:
            parts.append('opis')
        if 'slika' in changed:
            parts.append('glavna slika')
        if 'je_novitet' in changed:
            parts.append('noviteti ' + ('uključeno' if product.je_novitet else 'isključeno'))
        if 'je_hit' in changed:
            parts.append('HIT ponuda ' + ('uključeno' if product.je_hit else 'isključeno'))
        if 'pakovanje' in changed:
            if product.pakovanje_komada and product.pakovanje_komada > 1:
                parts.append(f'pakovanje {product.pakovanje_komada} kom.')
            else:
                parts.append('pakovanje isključeno')
        if extra_n:
            parts.append(f'+{extra_n} slika')
        messages.success(
            request,
            'Sačuvano: ' + (', '.join(parts) if parts else 'bez izmjena') + '.',
        )
    # Legacy single-field actions (zadržano radi kompatibilnosti)
    elif action == 'set_price':
        raw_price = (request.POST.get('cijena') or '').strip().replace(',', '.')
        try:
            new_price = Decimal(raw_price)
        except (InvalidOperation, ValueError):
            messages.error(request, 'Unesite ispravnu cijenu (npr. 45.00).')
            return redirect('product_detail', slug=slug)
        if new_price <= 0:
            messages.error(request, 'Cijena mora biti veća od 0.')
            return redirect('product_detail', slug=slug)
        product.cijena = new_price
        if product.akcija_postotak:
            product.akcijska_cijena = None
        product.save()
        messages.success(request, f'Cijena ažurirana na {new_price} KM.')
    elif action == 'set_category':
        raw_id = (request.POST.get('kategorija_id') or '').strip()
        if not raw_id:
            product.kategorija = None
            product.save(update_fields=['kategorija'])
            messages.success(request, 'Kategorija uklonjena sa artikla.')
        else:
            try:
                category_id = int(raw_id)
            except (TypeError, ValueError):
                messages.error(request, 'Odaberite ispravnu kategoriju.')
                return redirect('product_detail', slug=slug)
            category = Category.objects.filter(pk=category_id).first()
            if not category:
                messages.error(request, 'Kategorija nije pronađena.')
                return redirect('product_detail', slug=slug)
            product.kategorija = category
            product.save(update_fields=['kategorija'])
            messages.success(request, f'Kategorija postavljena na „{category}”.')
    elif action == 'set_brand':
        raw_id = (request.POST.get('brend_id') or '').strip()
        if not raw_id:
            product.brend = None
            product.save(update_fields=['brend'])
            messages.success(request, 'Brend uklonjen sa artikla.')
        else:
            try:
                brand_id = int(raw_id)
            except (TypeError, ValueError):
                messages.error(request, 'Odaberite ispravan brend.')
                return redirect('product_detail', slug=slug)
            brand = Brand.objects.filter(pk=brand_id).first()
            if not brand:
                messages.error(request, 'Brend nije pronađen.')
                return redirect('product_detail', slug=slug)
            product.brend = brand
            product.save(update_fields=['brend'])
            messages.success(request, f'Brend postavljen na „{brand.naziv}”.')
    elif action == 'set_opis':
        product.opis = (request.POST.get('opis') or '').strip()
        product.save(update_fields=['opis'])
        messages.success(request, 'Opis artikla je ažuriran.')
    elif action == 'upload_main_image':
        uploaded = request.FILES.get('glavna_slika')
        if not uploaded:
            messages.error(request, 'Odaberite glavnu sliku.')
            return redirect('product_detail', slug=slug)
        if not _staff_upload_is_image(uploaded):
            messages.error(request, 'Datoteka mora biti slika.')
            return redirect('product_detail', slug=slug)
        product.slika = uploaded
        product.save()
        messages.success(request, 'Glavna slika je ažurirana.')
    elif action == 'upload_extra_images':
        uploads = request.FILES.getlist('dodatne_slike')
        if not uploads:
            messages.error(request, 'Odaberite barem jednu dodatnu sliku.')
            return redirect('product_detail', slug=slug)
        max_order = (
            product.dodatne_slike.aggregate(max_red=Max('redoslijed')).get('max_red') or 0
        )
        created = 0
        for index, uploaded in enumerate(uploads, start=1):
            if not _staff_upload_is_image(uploaded):
                continue
            ProductImage.objects.create(
                product=product,
                slika=uploaded,
                redoslijed=max_order + index,
            )
            created += 1
        if not created:
            messages.error(request, 'Nijedna odabrana datoteka nije validna slika.')
            return redirect('product_detail', slug=slug)
        messages.success(request, f'Dodano {created} dodatnih slika.')
    elif action == 'delete_extra_image':
        raw_image_id = (request.POST.get('image_id') or '').strip()
        try:
            image_id = int(raw_image_id)
        except (TypeError, ValueError):
            messages.error(request, 'Slika nije pronađena.')
            return redirect('product_detail', slug=slug)
        image = ProductImage.objects.filter(pk=image_id, product=product).first()
        if not image:
            messages.error(request, 'Slika nije pronađena.')
            return redirect('product_detail', slug=slug)
        image.delete()
        messages.success(request, 'Dodatna slika je uklonjena.')
    elif action == 'set_tags':
        tag_ids = _staff_parse_tag_ids(request)
        tags = list(Tag.objects.filter(pk__in=tag_ids))
        product.tagovi.set(tags)
        messages.success(request, f'Tagovi ažurirani ({len(tags)}).')
    else:
        messages.error(request, 'Nepoznata akcija.')
    return redirect('product_detail', slug=slug)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def staff_category_search(request):
    query = request.GET.get('q', '').strip()
    categories_qs = Category.objects.select_related('roditelj')
    if query:
        categories_qs = categories_qs.filter(
            Q(naziv__icontains=query)
            | Q(slug__icontains=query)
            | Q(roditelj__naziv__icontains=query),
        )
    categories = list(
        categories_qs.order_by('roditelj__naziv', 'naziv')[:STAFF_LOOKUP_LIMIT],
    )
    return JsonResponse({
        'results': [{'id': category.pk, 'label': str(category)} for category in categories],
        'query': query,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def staff_tag_search(request):
    query = request.GET.get('q', '').strip()
    tags_qs = Tag.objects.select_related('roditelj')
    if query:
        tags_qs = tags_qs.filter(
            Q(naziv__icontains=query)
            | Q(slug__icontains=query)
            | Q(roditelj__naziv__icontains=query),
        )
    tags = list(tags_qs.order_by('roditelj__naziv', 'naziv')[:STAFF_LOOKUP_LIMIT])
    return JsonResponse({
        'results': [{'id': tag.pk, 'label': str(tag)} for tag in tags],
        'query': query,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def staff_brand_search(request):
    query = request.GET.get('q', '').strip()
    brands_qs = Brand.objects.all()
    if query:
        brands_qs = brands_qs.filter(
            Q(naziv__icontains=query) | Q(slug__icontains=query),
        )
    brands = list(brands_qs.order_by('naziv')[:STAFF_LOOKUP_LIMIT])
    return JsonResponse({
        'results': [{'id': brand.pk, 'label': brand.naziv} for brand in brands],
        'query': query,
    })


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_post_product_olx(request, slug):
    from django.utils import timezone

    if not _staff_edit_mode_enabled(request):
        messages.error(request, 'Edit mode je isključen. Uključi ga da bi mijenjao artikle.')
        return redirect('product_detail', slug=slug)

    product = get_object_or_404(
        Product.objects.select_related('brend').prefetch_related('dodatne_slike'),
        slug=slug,
    )
    try:
        result = publish_product_to_olx(product)
        product.olx_listing_id = result['id']
        product.olx_listing_slug = result.get('slug', '') or ''
        product.olx_listing_url = result.get('url', '') or ''
        product.olx_objavljen = timezone.now()
        product.save(update_fields=[
            'olx_listing_id', 'olx_listing_slug', 'olx_listing_url', 'olx_objavljen',
        ])
        if result.get('status') == 'active':
            messages.success(
                request,
                'Artikal je aktivan na OLX/Pik. Provjeri u aplikaciji: Moj OLX → Aktivni oglasi. '
                f'Pretraga: {result.get("url", "")}',
            )
        else:
            messages.warning(
                request,
                'Oglas je poslan na OLX/Pik, ali nije postao aktivan. '
                'Provjeri Neaktivne oglase u Pik/OLX aplikaciji ili kontaktiraj podršku. '
                f'Link: {result.get("url", "")}',
            )
    except OlxApiError as exc:
        messages.error(request, f'OLX/Pik objava nije uspjela: {exc}')
    except Exception:
        logger.exception('OLX objava artikla %s', slug)
        messages.error(request, 'OLX/Pik objava nije uspjela zbog neočekivane greške.')
    return redirect('product_detail', slug=slug)


@login_required(login_url='login')
@user_passes_test(_staff_required)
def staff_loyalty_system(request):
    from decimal import InvalidOperation
    from .loyalty import azuriraj_loyalty_karticu, loyalty_kontekst, osiguraj_loyalty_karticu

    issue_form = LoyaltyIssueForm()
    newly_issued = request.GET.get('issued') == '1'

    if request.method == 'POST' and request.POST.get('action') == 'aktiviraj_nalog':
        try:
            activate_user_id = int(request.POST.get('user_id') or 0)
        except (TypeError, ValueError):
            activate_user_id = 0
        target = User.objects.filter(
            pk=activate_user_id,
            is_superuser=False,
            is_staff=False,
        ).first()
        if not target:
            messages.error(request, 'Kupac nije pronađen.')
        elif target.is_active:
            messages.info(request, f'Nalog {target.email or target.username} je već aktivan.')
        else:
            target.is_active = True
            target.save(update_fields=['is_active'])
            messages.success(
                request,
                f'Nalog {target.email or target.username} je aktiviran. Kupac se sada može prijaviti.',
            )
        q = (request.POST.get('q') or request.GET.get('q') or '').strip()
        if q:
            return redirect(f'{request.path}?q={q}')
        return redirect(request.path)

    if request.method == 'POST' and request.POST.get('action') == 'izdaj_karticu':
        issue_form = LoyaltyIssueForm(request.POST)
        if issue_form.is_valid():
            try:
                card, user = izdaj_loyalty_karticu(
                    issue_form.cleaned_data['ime'],
                    issue_form.cleaned_data['prezime'],
                    issue_form.cleaned_data['telefon'],
                    issue_form.cleaned_data['email'],
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                sync_korisnik(user)
                messages.success(
                    request,
                    f'Kartica izdata za {user.get_full_name()}. Broj: {card.kod}',
                )
                return redirect(f"{request.path}?q={card.kod}&issued=1")
        else:
            messages.error(request, 'Provjerite unesene podatke i pokušajte ponovo.')

    q = (request.GET.get('q') or '').strip()
    cards = []
    selected_card = None
    user_orders = []
    loyalty_ctx = None
    edit_form = None
    cardholder_name = ''
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
            cardholder_name = (
                selected_card.user.get_full_name().strip()
                or (selected_card.user.email or '').strip().lower()
            )

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
                    from .loyalty import email_vec_registrovan, telefon_vec_registrovan

                    u = selected_card.user
                    new_email = edit_form.cleaned_data.get('email', u.email).strip().lower()
                    new_phone = edit_form.cleaned_data.get('telefon', '')
                    if email_vec_registrovan(new_email, exclude_user_id=u.pk):
                        messages.error(request, 'Ovaj email je već registrovan na drugoj kartici.')
                        return redirect(f"{request.path}?q={q}")
                    if new_phone and telefon_vec_registrovan(new_phone, exclude_user_id=u.pk):
                        messages.error(request, 'Ovaj broj telefona je već registrovan na drugoj kartici.')
                        return redirect(f"{request.path}?q={q}")

                    ime_prezime = edit_form.cleaned_data.get('ime_prezime', '').strip()
                    if ime_prezime:
                        parts = ime_prezime.split(maxsplit=1)
                        u.first_name = parts[0]
                        u.last_name = parts[1] if len(parts) > 1 else ''
                    u.email = new_email
                    u.save(update_fields=['first_name', 'last_name', 'email'])

                    if profil:
                        profil.telefon = new_phone
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
        'issue_form': issue_form,
        'newly_issued': newly_issued,
        'cardholder_name': cardholder_name,
    }
    return render(request, 'staff/loyalty_system.html', context)


@login_required(login_url='login')
@user_passes_test(_staff_required)
@require_GET
def staff_loyalty_card_image(request, card_id):
    """PNG slika loyalty kartice s QR kodom i barkodom (za Viber / preuzimanje)."""
    from django.http import HttpResponse
    from .loyalty import generisi_loyalty_card_image

    card = get_object_or_404(
        LoyaltyCard.objects.select_related('user', 'user__profil'),
        pk=card_id,
    )
    name = card.user.get_full_name().strip() or (card.user.email or '').strip().lower()
    png = generisi_loyalty_card_image(card, cardholder_name=name)
    response = HttpResponse(png, content_type='image/png')
    response['Content-Disposition'] = f'inline; filename="loyalty-{card.kod}.png"'
    response['Cache-Control'] = 'private, max-age=60'
    return response


@login_required(login_url='login')
@user_passes_test(_staff_required)
@require_GET
def staff_loyalty_card_qr(request, card_id):
    """Samostalni QR PNG za ispis / prikaz na kartici."""
    import io

    from django.http import HttpResponse
    from .loyalty import _qr_image

    card = get_object_or_404(LoyaltyCard, pk=card_id)
    qr = _qr_image(card.kod, box_size=8, border=2)
    buffer = io.BytesIO()
    qr.save(buffer, format='PNG')
    response = HttpResponse(buffer.getvalue(), content_type='image/png')
    response['Content-Disposition'] = f'inline; filename="loyalty-qr-{card.kod}.png"'
    response['Cache-Control'] = 'private, max-age=120'
    return response


@login_required(login_url='login')
@require_GET
def staff_loyalty_card_barcode(request, card_id):
    """Code128 barkod PNG — vlasnik kartice ili staff."""
    from django.http import HttpResponse, HttpResponseForbidden
    from .loyalty import generisi_loyalty_barcode_png

    card = get_object_or_404(LoyaltyCard, pk=card_id)
    is_staff_user = request.user.is_authenticated and (
        request.user.is_superuser or request.user.is_staff
    )
    if not is_staff_user and card.user_id != request.user.pk:
        return HttpResponseForbidden('Nemate pristup ovoj kartici.')
    code = card.barkod or card.kod
    png = generisi_loyalty_barcode_png(code)
    response = HttpResponse(png, content_type='image/png')
    response['Content-Disposition'] = f'inline; filename="loyalty-barcode-{code}.png"'
    response['Cache-Control'] = 'private, max-age=120'
    return response
