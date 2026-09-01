import json
import logging
import random
import re
import uuid
import requests
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from urllib.parse import urlencode, urlparse

from django.conf import settings
from .models import SiteSettings
from django import forms as django_forms
from django.core.files.base import ContentFile
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.db.models import Case, Count, Exists, F, IntegerField, Max, OuterRef, Prefetch, Q, Value, When
from django.utils import timezone
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

from .cart import Cart, stock_limit_message, stock_on_hand
from .category_visibility import filter_categories_with_products, get_category_ids_with_products
from .loyalty import (
    azuriraj_loyalty_nakon_narudzbe,
    izdaj_loyalty_karticu,
    kreiraj_loyalty_karticu,
    loyalty_kontekst,
    maybe_apply_loyalty_coupon_from_phone,
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
from .product_options import find_similar_name_products
from .utils.images import image_field_dimensions
from .utils.seo import (
    auto_category_seo_description,
    auto_category_seo_title,
    breadcrumb_json_ld,
    collection_page_json_ld,
    entity_seo_context,
    json_ld,
    page_seo_context,
    product_json_ld,
)

logger = logging.getLogger(__name__)
from .forms import (
    CheckoutForm,
    CouponForm,
    LoginForm,
    LoyaltyIssueForm,
    ProfileForm,
    StaffLoyaltyProfileForm,
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
    HomePromoCard,
    HomeTrustItem,
    HomeVlog,
    Coupon,
    LiveVisitor,
    LoyaltyCard,
    MarketingSubscriber,
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
    return qs.select_related('kategorija', 'kategorija__roditelj', 'brend').annotate(
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
    """Superuser Edit mode na sajtu (checkbox ispod korpe)."""
    if not _request_is_superuser(request):
        return False
    return bool(request.session.get(STAFF_EDIT_MODE_SESSION_KEY))


def _can_view_out_of_stock(request=None):
    """
    Superuser u Edit mode vidi i neaktivne artikle (product_detail).
    Artikli van stanja su javno vidljivi; korpa je i dalje blokirana.
    """
    return _staff_edit_mode_enabled(request)


def _invalidate_storefront_product_caches():
    from django.core.cache import cache

    from .category_visibility import invalidate_category_product_cache

    invalidate_category_product_cache()
    for key in (
        'home_latest_products_v3',
        'home_featured_products_v3',
        'home_sale_products_v3',
        'home_brand_show_v3',
        'showcase_brands_v2',
        'home_cat_show_v4:6',
    ):
        cache.delete(key)


def _product_queryset(request=None):
    """Aktivni artikli na sajtu — i oni koji nisu na stanju (bez korpe)."""
    qs = Product.objects.filter(aktivan=True)
    if not _staff_edit_mode_enabled(request):
        qs = qs.filter(sakriven_do_stanja=False)
    return _prefetch_product_cards(qs)


def _bind_variation_parents(product):
    """Poveži prefetched varijacije s parentom — bez N+1 na variation.artikal."""
    try:
        for variation in product.varijacije.all():
            variation.artikal = product
    except Exception:
        pass


def _effective_product_price(product):
    variations = list(product.varijacije.all())
    if variations:
        for variation in variations:
            variation.artikal = product
        return min(variation.prikazna_cijena for variation in variations)
    return product.prikazna_cijena


def _product_is_on_sale(product):
    variations = list(product.varijacije.all())
    if variations:
        for variation in variations:
            variation.artikal = product
        return any(variation.na_akciji for variation in variations)
    return product.na_akciji


def _akcija_products_qs(products_qs):
    """Artikli na akciji — u SQL-u, bez učitavanja cijelog kataloga u Python."""
    today = timezone.localdate()
    product_sale = (
        Q(akcijska_cijena__isnull=False)
        & Q(akcijska_cijena__lt=F('cijena'))
        & (Q(akcija_do__isnull=True) | Q(akcija_do__gte=today))
    )
    variation_own_price = (
        Q(varijacije__akcijska_cijena__isnull=False)
        & Q(varijacije__cijena__isnull=False)
        & Q(varijacije__akcijska_cijena__lt=F('varijacije__cijena'))
    )
    variation_inherit_price = (
        Q(varijacije__akcijska_cijena__isnull=False)
        & Q(varijacije__cijena__isnull=True)
        & Q(varijacije__akcijska_cijena__lt=F('cijena'))
    )
    return products_qs.filter(
        product_sale | variation_own_price | variation_inherit_price
    ).distinct()


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
    """Brendovi s logom + artiklima — cache da DISTINCT ne usporava svaki home load."""
    from django.core.cache import cache

    cache_key = 'showcase_brands_v2'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Brži put: brendovi koji imaju logo, pa filter po postojanju artikla
    brand_ids = (
        Product.objects.filter(aktivan=True, sakriven_do_stanja=False)
        .exclude(brend_id__isnull=True)
        .values_list('brend_id', flat=True)
        .distinct()
    )
    brands = list(
        Brand.objects.filter(id__in=brand_ids)
        .exclude(slika='')
        .exclude(slika__isnull=True)
        .order_by('naziv')
    )
    cache.set(cache_key, brands, 300)
    return brands


# BH/HR dijakritici → ASCII (štap ≈ stap)
_SEARCH_DIACRITIC_MAP = str.maketrans({
    'š': 's', 'đ': 'd', 'č': 'c', 'ć': 'c', 'ž': 'z',
    'Š': 's', 'Đ': 'd', 'Č': 'c', 'Ć': 'c', 'Ž': 'z',
})


def _search_fold(value):
    if not value:
        return ''
    return str(value).casefold().translate(_SEARCH_DIACRITIC_MAP)


def _normalize_phrase(value):
    """Suzi višestruke razmake (uklj. NBSP) — fraza ostaje jedan tag."""
    if not value:
        return ''
    text = str(value).replace('\u00a0', ' ').replace('\u200b', '')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return re.sub(r'\s+', ' ', text.strip())


def _phrase_key(value):
    """
    Kanonski ključ za usporedbu višerječnih tagova / upita.
    „Varalica  za  more” == „varalica za more” (fold + razmaci).
    Cijela fraza = JEDAN tag — ne dijeli se na riječi pri matchu.
    """
    phrase = _normalize_phrase(value)
    if not phrase:
        return ''
    return _search_fold(phrase)


def _parse_category_search_tags(raw):
    """
    Tagovi podkategorije. Separatori: SAMO zarez, novi red, ;, | .
    Razmaci OSTAJU — „varalica za more” je JEDAN tag, ne tri riječi.
    """
    if not raw:
        return []
    text = str(raw).replace('\u00a0', ' ').replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace(';', '\n').replace('|', '\n')
    tags = []
    seen = set()
    for line in text.split('\n'):
        for part in line.split(','):
            tag = _normalize_phrase(part)
            if not tag:
                continue
            key = _phrase_key(tag)
            if not key or key in seen:
                continue
            seen.add(key)
            tags.append(tag)
    return tags


# Tag: max ±3 slova, manje na kratkim riječima
_TAG_MAX_EDIT_DISTANCE = 3


def _allowed_edit_distance_for_len(n):
    if n <= 0:
        return 0
    if n <= 4:
        return 1
    if n <= 8:
        return 2
    return _TAG_MAX_EDIT_DISTANCE


def _edit_distance(a, b, *, max_dist=None):
    """Levenshtein udaljenost (insert/delete/replace = 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    cap = max_dist if max_dist is not None else max(len(a), len(b))
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            ins = curr[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(ins, delete, sub))
        prev = curr
    return prev[-1]


def _words_within_edit(tag_words, q_words):
    if len(tag_words) != len(q_words) or not q_words:
        return False
    for tw, qw in zip(tag_words, q_words):
        allow = _allowed_edit_distance_for_len(max(len(tw), len(qw)))
        if _edit_distance(tw, qw, max_dist=allow) > allow:
            return False
    return True


def _word_fuzzy_eq(a, b):
    """Dvije riječi jednake ili unutar dozvoljene edit distance."""
    if not a or not b:
        return False
    if a == b:
        return True
    allow = _allowed_edit_distance_for_len(max(len(a), len(b)))
    return _edit_distance(a, b, max_dist=allow) <= allow


def _tag_matches_query(tag, query):
    """
    Tag (jedna ili više riječi) ≈ upit.

    Primjeri:
      tag „fider strune” + upit „fider strune” → da
      tag „fider strune” + upit „fider”        → da
      tag „fider strune” + upit „strune”       → da
      tag „fider strune” + upit „feeder”       → da (fuzzy po riječi)
      tag „som” + upit „som”                   → da

    Višerječni tagovi se NE dijele zarezom — razmak ostaje unutar jednog taga.
    """
    tag_f = _search_fold(_normalize_phrase(tag))
    q_f = _search_fold(_normalize_phrase(query))
    if not tag_f or not q_f or len(q_f) < 2:
        return False

    # 1) Tačan cijeli tag
    if tag_f == q_f:
        return True

    # 2) Cijeli string ± edit distance (kratki tagovi)
    allow_full = _allowed_edit_distance_for_len(max(len(tag_f), len(q_f)))
    # Za duge fraze ne forsiraj full-string fuzzy (previše labavo)
    if max(len(tag_f), len(q_f)) <= 24:
        if _edit_distance(tag_f, q_f, max_dist=allow_full) <= allow_full:
            return True

    tag_words = [w for w in tag_f.split() if w]
    q_words = [w for w in q_f.split() if w]
    if not tag_words or not q_words:
        return False

    # 3) Ista broj riječi, svaka riječ fuzzy (npr. „fider strune” ≈ „feeder strune”)
    if _words_within_edit(tag_words, q_words):
        return True

    # 4) Fraza: upit je podniz riječi taga (npr. „fider” u „fider strune”,
    #    ili „fider strune” unutar duljeg taga)
    n_t, n_q = len(tag_words), len(q_words)
    if n_q <= n_t:
        for i in range(n_t - n_q + 1):
            window = tag_words[i:i + n_q]
            if _words_within_edit(window, q_words):
                return True

    # 5) Fraza obrnuto: tag je podniz riječi upita
    if n_t <= n_q:
        for i in range(n_q - n_t + 1):
            window = q_words[i:i + n_t]
            if _words_within_edit(tag_words, window):
                return True

    # 6) Sve riječi upita postoje u tagu (bilo kojim redom), fuzzy
    if n_q >= 1 and n_q <= n_t:
        used = [False] * n_t
        all_found = True
        for qw in q_words:
            found = False
            for i, tw in enumerate(tag_words):
                if used[i]:
                    continue
                if _word_fuzzy_eq(tw, qw):
                    used[i] = True
                    found = True
                    break
            if not found:
                all_found = False
                break
        if all_found:
            return True

    # 7) Jedna riječ upita = jedna riječ taga (fuzzy) — „fider” / „strune”
    #    samo ako je upit jedna riječ (inače bi previše labavo bilo)
    if n_q == 1:
        qw = q_words[0]
        for tw in tag_words:
            if _word_fuzzy_eq(tw, qw):
                return True
            # prefiks riječi (min 3 znaka) — „fid” ne, „fide” da za „fider”
            if len(qw) >= 3 and (tw.startswith(qw) or qw.startswith(tw)):
                return True

    # 8) Podstring fraze (bez razbijanja riječi na slova u sredini)
    #    „fider str” u „fider strune”
    if len(q_f) >= 3 and (q_f in tag_f or tag_f in q_f):
        return True

    return False


_TAG_CACHE = None
_TAG_CACHE_AT = 0.0
_TAG_CACHE_TTL_SEC = 60.0


def _cached_tags():
    """In-memory lista tagova (id, naziv) — izbjegava full scan DB po svakom keystroke-u."""
    global _TAG_CACHE, _TAG_CACHE_AT
    import time

    now = time.monotonic()
    if _TAG_CACHE is None or (now - _TAG_CACHE_AT) > _TAG_CACHE_TTL_SEC:
        from .models import Tag

        _TAG_CACHE = list(Tag.objects.only('id', 'naziv'))
        _TAG_CACHE_AT = now
    return _TAG_CACHE


def _product_tag_ids_for_query(query):
    """
    ID-evi Tag modela na artiklima koji odgovaraju upitu.
    Podržava višerječne tagove (npr. „fider strune”).
    """
    q = _normalize_phrase(query)
    if not q or len(q) < 2:
        return []
    return [
        tag.pk
        for tag in _cached_tags()
        if _tag_matches_query(tag.naziv or '', q)
    ]


def _invalidate_search_tag_caches():
    """Reset in-memory tag/category tag cache (npr. nakon admin izmjene)."""
    global _TAG_CACHE, _TAG_CACHE_AT, _CAT_SEARCH_TAG_CACHE, _CAT_SEARCH_TAG_CACHE_AT
    _TAG_CACHE = None
    _TAG_CACHE_AT = 0.0
    _CAT_SEARCH_TAG_CACHE = None
    _CAT_SEARCH_TAG_CACHE_AT = 0.0
    # Query-level caches (exact/fuzzy cat ids per upit)
    try:
        _exact_cat_ids_for_query_cached.cache_clear()
        _fuzzy_cat_ids_for_query_cached.cache_clear()
    except Exception:
        pass


# —— Search score bands ——
# Prioritet: 1) šifra / naziv  2) tek onda tag potkategorije
SEARCH_SCORE = {
    'exact_sifra': 1100,       # šifra tačno
    'exact_name': 1050,        # naziv tačno
    'sifra_partial': 1000,     # šifra djelomično
    'name_startswith': 950,    # naziv počinje upitom
    # Sve riječi upita u nazivu, redoslijed nije bitan (itana feeder ⊆ … ITANA … Feeder …)
    'name_all_words': 920,
    'name_contains': 880,      # cijela fraza / podstring u nazivu
    # Tagovi artikla (M2M) — ispod naziva/šifre, iznad tagova potkategorije
    'exact_product_tag': 800,
    'product_tag': 760,
    # Tagovi potkategorije — ispod naziva i šifre
    'exact_category_tag': 750,
    'category_tag': 700,
}


_CAT_SEARCH_TAG_CACHE = None
_CAT_SEARCH_TAG_CACHE_AT = 0.0
_CAT_SEARCH_TAG_CACHE_TTL_SEC = 60.0


def _cached_category_search_tags():
    """
    Lista (category_id, parent_id, [search_tags], naziv) za brzo matchanje
    category search_tagovi bez N+1.
    """
    global _CAT_SEARCH_TAG_CACHE, _CAT_SEARCH_TAG_CACHE_AT
    import time

    now = time.monotonic()
    if (
        _CAT_SEARCH_TAG_CACHE is None
        or (now - _CAT_SEARCH_TAG_CACHE_AT) > _CAT_SEARCH_TAG_CACHE_TTL_SEC
    ):
        from .models import Category

        rows = []
        for cat in Category.objects.filter(aktivan=True).only(
            'id', 'roditelj_id', 'search_tagovi', 'naziv',
        ):
            tags = []
            if hasattr(cat, 'search_tagovi_list'):
                raw_tags = cat.search_tagovi_list
                if callable(raw_tags):
                    raw_tags = raw_tags()
                tags = list(raw_tags or [])
            if not tags and cat.search_tagovi:
                tags = _parse_category_search_tags(cat.search_tagovi)
            rows.append({
                'id': cat.pk,
                'parent_id': cat.roditelj_id,
                'tags': tags,
                'naziv': cat.naziv or '',
            })
        _CAT_SEARCH_TAG_CACHE = rows
        _CAT_SEARCH_TAG_CACHE_AT = now
    return _CAT_SEARCH_TAG_CACHE


def _expand_category_ids_with_descendants(cat_ids):
    """
    Proširi skup ID-eva potkategorija na sve podnivoe
    (cijelo stablo ispod matchane potkategorije).
    """
    if not cat_ids:
        return set()
    expanded = set(cat_ids)
    rows = _cached_category_search_tags()
    # BFS preko parent_id u cache-u (bez N+1 na get_descendant_ids)
    by_parent = {}
    for row in rows:
        pid = row.get('parent_id')
        if pid:
            by_parent.setdefault(pid, []).append(row['id'])
    queue = list(cat_ids)
    seen = set(cat_ids)
    while queue:
        cur = queue.pop()
        for child_id in by_parent.get(cur, ()):
            if child_id not in seen:
                seen.add(child_id)
                expanded.add(child_id)
                queue.append(child_id)
    # Dopuna iz ORM (ako cache nema neke grane)
    try:
        from .models import Category
        for cat in Category.objects.filter(pk__in=list(cat_ids), aktivan=True):
            try:
                expanded.update(cat.get_descendant_ids())
            except Exception:
                pass
    except Exception:
        pass
    return expanded


def _is_multiword_phrase(value):
    """True ako fraza ima 2+ riječi (višerječni tag / upit)."""
    return len(_normalize_phrase(value).split()) >= 2


def _subcategory_ids_for_exact_tag(query):
    """
    Potkategorije čiji je search-tag 100% jednak cijeloj frazi upita.
    Cached po upitu — score po artiklu inače ponavlja isti scan.
    """
    q = _normalize_phrase(query)
    if not q or len(q) < 2:
        return set()
    return set(_exact_cat_ids_for_query_cached(_phrase_key(q) or q.casefold()))


@lru_cache(maxsize=256)
def _exact_cat_ids_for_query_cached(q_key: str) -> frozenset:
    """Internal: exact multi/single-word tag → category ids (+ descendants)."""
    if not q_key or len(q_key) < 2:
        return frozenset()

    direct = set()
    for row in _cached_category_search_tags():
        if not row.get('parent_id') or not row.get('tags'):
            continue
        for tag in row['tags']:
            tag_key = _phrase_key(tag)
            if tag_key == q_key:
                direct.add(row['id'])
                break
            if (
                tag_key
                and len(q_key) >= 6
                and abs(len(tag_key) - len(q_key)) <= 2
            ):
                allow = min(2, _allowed_edit_distance_for_len(max(len(tag_key), len(q_key))))
                if _edit_distance(tag_key, q_key, max_dist=allow) <= allow:
                    direct.add(row['id'])
                    break
    return frozenset(_expand_category_ids_with_descendants(direct))


@lru_cache(maxsize=256)
def _fuzzy_cat_ids_for_query_cached(q_key: str) -> frozenset:
    """Jednorječni fuzzy tag match (cached)."""
    if not q_key or len(q_key) < 2 or ' ' in q_key:
        return frozenset()
    direct = set()
    for row in _cached_category_search_tags():
        if not row.get('parent_id') or not row.get('tags'):
            continue
        for tag in row['tags']:
            if _tag_matches_category_search_tag(tag, q_key):
                direct.add(row['id'])
                break
    return frozenset(_expand_category_ids_with_descendants(direct))


def _tag_matches_category_search_tag(tag, query):
    """
    Match search-taga potkategorije.

    Višerječni upit (npr. „varalica za more”):
      - SAMO cijela fraza = cijeli tag (jedan tag)
      - NIKAD: riječi varalica / za / more odvojeno
      - NIKAD: kraći tag unutar duljeg upita

    Jednorječni upit: fuzzy (_tag_matches_query).
    """
    tag_phrase = _normalize_phrase(tag)
    q_phrase = _normalize_phrase(query)
    if not tag_phrase or not q_phrase or len(q_phrase) < 2:
        return False

    # Višerječni upit → isključivo cjelovita fraza (jedan tag)
    if _is_multiword_phrase(q_phrase):
        q_key = _phrase_key(q_phrase)
        tag_key = _phrase_key(tag_phrase)
        if not q_key or not tag_key:
            return False
        if tag_key == q_key:
            return True
        # Samo blagi typo na CIJELOJ frazi (ne po riječima)
        if len(q_key) >= 6 and abs(len(tag_key) - len(q_key)) <= 2:
            allow = min(2, _allowed_edit_distance_for_len(max(len(tag_key), len(q_key))))
            if _edit_distance(tag_key, q_key, max_dist=allow) <= allow:
                return True
        return False

    # Jedna riječ upita — klasični fuzzy (kapa, spod, som…)
    return _tag_matches_query(tag_phrase, q_phrase)


def _category_ids_for_search_query(query, *, exact_only=False):
    """
    Potkategorije čiji search_tagovi odgovaraju upitu (cached po upitu).

    Višerječni upit: samo tačan višerječni tag (cijela fraza).
    Jednorječni: fuzzy na jednorječne tagove.
    """
    q = _normalize_phrase(query)
    if not q or len(q) < 2:
        return set()

    exact = _subcategory_ids_for_exact_tag(q)
    if exact_only or exact:
        return exact

    if _is_multiword_phrase(q):
        return set()

    return set(_fuzzy_cat_ids_for_query_cached(_phrase_key(q) or q.casefold()))


# —— Tagovi artikla (Product.tagovi M2M) ——
_PRODUCT_TAG_CACHE = None
_PRODUCT_TAG_CACHE_AT = 0.0
_PRODUCT_TAG_CACHE_TTL_SEC = 45.0


def _cached_product_tags():
    """Lista {id, naziv} svih Tag zapisa (brzo matchanje bez N+1)."""
    global _PRODUCT_TAG_CACHE, _PRODUCT_TAG_CACHE_AT
    import time

    now = time.monotonic()
    if (
        _PRODUCT_TAG_CACHE is None
        or (now - _PRODUCT_TAG_CACHE_AT) > _PRODUCT_TAG_CACHE_TTL_SEC
    ):
        from .models import Tag

        _PRODUCT_TAG_CACHE = [
            {'id': tid, 'naziv': name or ''}
            for tid, name in Tag.objects.values_list('id', 'naziv')
        ]
        _PRODUCT_TAG_CACHE_AT = now
    return _PRODUCT_TAG_CACHE


def invalidate_product_tag_search_cache():
    global _PRODUCT_TAG_CACHE, _PRODUCT_TAG_CACHE_AT
    _PRODUCT_TAG_CACHE = None
    _PRODUCT_TAG_CACHE_AT = 0.0
    _product_ids_for_tag_query_cached.cache_clear()


@lru_cache(maxsize=256)
def _product_ids_for_tag_query_cached(q_key: str) -> frozenset:
    """
    Artikli čiji M2M tag odgovara upitu (isti match engine kao potkategorija tagovi).
    q_key = _phrase_key(upit) — već foldan.
    """
    if not q_key or len(q_key) < 2:
        return frozenset()

    # Match funkcije same rade fold/normalize — prosljeđujemo q_key kao upit
    query = q_key
    matching_tag_ids = []
    for row in _cached_product_tags():
        name = row.get('naziv') or ''
        if not name:
            continue
        if _tag_matches_category_search_tag(name, query):
            matching_tag_ids.append(row['id'])
            continue
        # Jednorječni: širi fuzzy (npr. „shimano” ≈ „Shimano”)
        if ' ' not in query and _tag_matches_query(name, query):
            matching_tag_ids.append(row['id'])

    if not matching_tag_ids:
        return frozenset()

    from .models import Product

    return frozenset(
        Product.objects.filter(tagovi__id__in=matching_tag_ids)
        .values_list('id', flat=True)
        .distinct(),
    )


def _product_ids_for_product_tag_query(query):
    raw = _normalize_phrase(query)
    if not raw or len(raw) < 2:
        return set()
    key = _phrase_key(raw) or raw.casefold()
    ids = set(_product_ids_for_tag_query_cached(key))
    # Dopuna: folded drugačiji od raw (diakritika)
    folded_raw = _search_fold(raw)
    if folded_raw and folded_raw != key:
        ids |= set(_product_ids_for_tag_query_cached(folded_raw))
    return ids


def _product_tag_match_level(product, query):
    """
    2 = tačan match taga artikla
    1 = fuzzy / djelomični match taga artikla
    0 = nema
    """
    raw = _normalize_phrase(query)
    if not raw or len(raw) < 2:
        return 0
    try:
        tags = list(product.tagovi.all())
    except Exception:
        return 0
    if not tags:
        return 0

    best = 0
    q_key = _phrase_key(raw) or raw.casefold()
    for tag in tags:
        name = getattr(tag, 'naziv', None) or str(tag)
        name_key = _phrase_key(name) or (name or '').casefold()
        if name_key and q_key and name_key == q_key:
            return 2
        if _tag_matches_category_search_tag(name, raw) or _tag_matches_query(name, raw):
            best = max(best, 1)
    return best


def _text_has_query(haystack, query_folded, *, as_word=False):
    """Da li folded haystack sadrži folded query (opcionalno kao riječ)."""
    hay = _search_fold(haystack or '')
    q = query_folded or ''
    if not hay or not q or len(q) < 2:
        return False
    if as_word:
        return re.search(
            rf'(?<![\w]){re.escape(q)}(?![\w])',
            hay,
            flags=re.UNICODE,
        ) is not None
    return q in hay


# Kratke riječi koje se NE koriste same za OR match (inače „za” hvata sve)
_SEARCH_STOPWORDS = frozenset({
    'za', 'i', 'u', 'na', 'od', 'do', 'sa', 's', 'ili', 'the', 'a', 'an', 'of', 'to',
})


def _search_tokens(raw):
    """
    Tokeni upita: slova/brojevi, fold, bez stop-riječi.
    „itana spin” → ['itana', 'spin']  (redoslijed se ne koristi kao fraza)
    """
    folded = _search_fold(_normalize_phrase(raw))
    tokens = []
    seen = set()
    for w in re.findall(r'[a-z0-9]+', folded):
        if len(w) < 2 or w in _SEARCH_STOPWORDS:
            continue
        if w in seen:
            continue
        seen.add(w)
        tokens.append(w)
    return tokens


def _search_significant_words(raw):
    """Riječi upita duže od 1 znaka, bez stop-riječi (za, i, na…)."""
    return _search_tokens(raw)


def _q_name_contains(term):
    """Naziv (ili normalizirani naziv) sadrži term."""
    term = (term or '').strip()
    if not term:
        return Q(pk__in=[])
    return Q(naziv__icontains=term) | Q(naziv_normalized__icontains=term)


def _q_sifra_contains(term):
    term = (term or '').strip()
    if not term:
        return Q(pk__in=[])
    variation_sifra = ProductVariation.objects.filter(
        artikal_id=OuterRef('pk'),
        sifra__icontains=term,
    )
    return (
        Q(sifra__icontains=term)
        | Q(sifra_normalized__icontains=term)
        | Exists(variation_sifra)
    )


def _q_name_or_sifra_contains(term):
    """Naziv ili šifra sadrži term (case-insensitive)."""
    return _q_name_contains(term) | _q_sifra_contains(term)


def _name_all_tokens_q(raw):
    """
    Naziv sadrži SVE tokene upita. Redoslijed nije bitan.

    „itana spin” ⊆ „MATE itana tournament spin”
    „tournament spin” ⊆ isto
    """
    tokens = _search_tokens(raw)
    if not tokens:
        return Q(pk__in=[])
    and_q = Q()
    for token in tokens:
        and_q &= _q_name_contains(token)
    return and_q


def _name_or_sifra_has_all_words(raw):
    """Naziv ima sve riječi, ili šifra ima cijeli upit."""
    raw = _normalize_phrase(raw)
    match = _name_all_tokens_q(raw)
    if raw:
        match |= _q_sifra_contains(raw)
    return match


def _search_exists_match(raw):
    """
    Primarno NAZIV: svaka riječ upita mora biti u nazivu (bilo kojim redom).

    „itana” → svi nazivi s itana
    „tournament spin” → nazivi koji imaju i tournament i spin
    „itana spin” → nazivi koji imaju i itana i spin
    Šifra ostaje kao dodatni match (cijeli upit).
    """
    raw = _normalize_phrase(raw)
    if not raw:
        return Q(pk__in=[])
    match = _name_all_tokens_q(raw)
    match |= _q_sifra_contains(raw)
    folded = _search_fold(raw)
    if folded and folded != raw:
        match |= _q_sifra_contains(folded)
    return match


def _apply_search_filter(products_qs, query):
    """
    Pretraga (prioritet):
    1) naziv artikla — podstring / riječi (AND)
    2) šifra artikla / varijacije
    3) tagovi artikla (M2M Tag)
    4) search tagovi potkategorije

    Primjer: „MATE” → svi artikli s „mate” u nazivu.
    """
    raw = _normalize_phrase(query)
    if not raw:
        return products_qs
    if len(raw) < 2:
        return products_qs.none()

    folded_raw = _search_fold(raw)
    tokens = _search_tokens(raw)

    # 1–2) Naziv (sve riječi, bilo kojim redom) + šifra
    match = _search_exists_match(raw)

    # Višerječni upit ostaje na nazivu/šifri — tagovi ne smiju preplaviti listu.
    if len(tokens) >= 2:
        return products_qs.filter(match).distinct()

    # 3) Tagovi artikla (M2M) — samo za jednu riječ
    try:
        product_tag_ids = _product_ids_for_product_tag_query(raw)
        if product_tag_ids:
            match |= Q(pk__in=product_tag_ids)
    except Exception:
        pass

    # 4) Tagovi potkategorije — samo za jednu riječ
    try:
        exact_cat_ids = _subcategory_ids_for_exact_tag(raw)
        if folded_raw and folded_raw != raw.casefold():
            exact_cat_ids = exact_cat_ids | _subcategory_ids_for_exact_tag(folded_raw)
        if exact_cat_ids:
            match |= Q(kategorija_id__in=exact_cat_ids)
        else:
            fuzzy_cat_ids = _category_ids_for_search_query(raw)
            if folded_raw and folded_raw != raw.casefold():
                fuzzy_cat_ids = fuzzy_cat_ids | _category_ids_for_search_query(folded_raw)
            if fuzzy_cat_ids:
                match |= Q(kategorija_id__in=fuzzy_cat_ids)
    except Exception:
        pass

    return products_qs.filter(match).distinct()


def _product_lager_priority(product):
    """0=normal, 1=favorizuj, 2=hit redukovanje — samo za sort među relevantnim."""
    try:
        return int(getattr(product, 'prioritet_lagera', 0) or 0)
    except (TypeError, ValueError):
        return 0


def _product_subcategory_tag_match_level(product, query):
    """
    2 = artikal u potkategoriji s TAČNIM search-tagom
    1 = fuzzy match taga potkategorije
    0 = nema

    Koristi cached set ID-eva — O(1) po artiklu.
    """
    raw = _normalize_phrase(query)
    if not raw or len(raw) < 2:
        return 0
    if not getattr(product, 'kategorija_id', None):
        return 0

    exact_ids = _subcategory_ids_for_exact_tag(raw)
    if product.kategorija_id in exact_ids:
        return 2
    if exact_ids:
        return 0

    fuzzy_ids = _category_ids_for_search_query(raw)
    if product.kategorija_id in fuzzy_ids:
        return 1
    return 0


def _tokenize_for_match(text):
    """Riječi iz naziva/upita (slova, brojevi; razdvoji i interpunkciju)."""
    folded = _search_fold(text or '')
    if not folded:
        return []
    # 2.13m → 2, 13, m  ili zadrži alnum komade
    parts = re.findall(r'[a-z0-9]+', folded, flags=re.UNICODE)
    return [p for p in parts if p]


def _name_has_all_significant_words(name, query):
    """
    Sve značajne riječi upita postoje u nazivu.
    Redoslijed nije bitan; riječi između se preskaču.

    Upit „itana feeder” na
    „MT13723 MATE ITANA Tournament Spin … Feeder …” → True
    (prva i treća / bilo koje pozicije — bitno da sve riječi upita postoje).
    """
    words = _search_significant_words(query)
    if not words:
        return False

    name_f = _search_fold(name or '')
    if not name_f:
        return False

    name_tokens = _tokenize_for_match(name)
    name_token_set = set(name_tokens)

    for w in words:
        wf = _search_fold(w)
        if not wf:
            return False
        # Tačan token
        if wf in name_token_set:
            continue
        # Prefiks tokena (min 3 znaka) — npr. „itana” u tokenu
        if len(wf) >= 3 and any(
            tok == wf or tok.startswith(wf) or wf.startswith(tok)
            for tok in name_tokens
            if len(tok) >= 3
        ):
            continue
        # Fallback: podstring u cijelom nazivu
        if wf in name_f:
            continue
        return False
    return True


def _search_relevance_score(product, query):
    """
    Bodovanje — prioritet: šifra i naziv, pa tag potkategorije.

    „itana feeder” na „… ITANA Tournament … Feeder …” → name_all_words (920)
    iznad tagova (750/700).
    """
    raw = _normalize_phrase(query)
    if not raw or len(raw) < 2:
        return 0

    q = raw.casefold()
    qf = _search_fold(raw)
    S = SEARCH_SCORE
    best = 0

    name = product.naziv or ''
    name_l = name.casefold()
    name_f = _search_fold(name)
    sifra = (product.sifra or '').strip()
    sifra_l = sifra.casefold()
    sifra_f = _search_fold(sifra)

    # —— Šifra (prioritet) ——
    if sifra and (sifra_l == q or sifra_f == qf):
        best = max(best, S['exact_sifra'])
    elif sifra and (
        q in sifra_l or qf in sifra_f
        or sifra_l.startswith(q) or sifra_f.startswith(qf)
    ):
        best = max(best, S['sifra_partial'])

    try:
        for v in product.varijacije.all():
            vs = (getattr(v, 'sifra', None) or '').strip()
            if not vs:
                continue
            vs_l = vs.casefold()
            vs_f = _search_fold(vs)
            if vs_l == q or vs_f == qf:
                best = max(best, S['exact_sifra'])
            elif q in vs_l or qf in vs_f or vs_l.startswith(q) or vs_f.startswith(qf):
                best = max(best, S['sifra_partial'])
    except Exception:
        pass

    # —— Naziv (prioritet iznad taga) ——
    if name_l == q or name_f == qf:
        best = max(best, S['exact_name'])
    elif name_l.startswith(q) or name_f.startswith(qf):
        best = max(best, S['name_startswith'])
    elif _name_has_all_significant_words(name, raw):
        # itana feeder → ITANA … Tournament … Feeder (redoslijed slobodan)
        best = max(best, S['name_all_words'])
    elif _text_has_query(name, qf, as_word=(len(qf) <= 4)) or (
        len(qf) > 4 and qf in name_f
    ):
        best = max(best, S['name_contains'])

    # —— Tagovi artikla (M2M) ——
    try:
        ptag = _product_tag_match_level(product, raw)
        if ptag >= 2:
            best = max(best, S['exact_product_tag'])
        elif ptag == 1:
            best = max(best, S['product_tag'])
    except Exception:
        pass

    # —— Tag potkategorije (ispod naziva/šifre) ——
    try:
        level = _product_subcategory_tag_match_level(product, raw)
        if level >= 2:
            best = max(best, S['exact_category_tag'])
        elif level == 1:
            best = max(best, S['category_tag'])
    except Exception:
        pass

    return best


def _sort_products_by_lager_priority(products, *, query='', price_sort=None):
    """
    Katalog / pretraga / kategorija:
    0) Ako ima search upit: relevantnost (score bandovi) prvo
    1) Hit redukovanje lagera → Favorizuj → Normal
    2) Unutar nivoa: cijena rastuće / opadajuće
    """
    if not products:
        return products

    # Prefetch za score (varijacije + tagovi artikla)
    if query or price_sort:
        try:
            from django.db.models import prefetch_related_objects

            relations = ['varijacije']
            if query:
                relations.append('tagovi')
            prefetch_related_objects(list(products), *relations)
        except Exception:
            pass

    # Cache cat/tag setova za cijeli sort (1× po upitu, ne N× po artiklu)
    if query:
        _subcategory_ids_for_exact_tag(query)
        _category_ids_for_search_query(query)
        _product_ids_for_product_tag_query(query)

    def key(p):
        rel = -_search_relevance_score(p, query) if query else 0
        prio = _product_lager_priority(p)
        name = (p.naziv or '').lower()
        try:
            _bind_variation_parents(p)
            price = float(_effective_product_price(p) or 0)
        except Exception:
            price = 0.0
        in_stock = 0 if getattr(p, 'na_stanju', False) else 1
        if price_sort == 'opadajuca':
            return (rel, in_stock, -prio, -price, name)
        return (rel, in_stock, -prio, price, name)

    return sorted(products, key=key)


def _sort_products_by_price(products, *, descending=False):
    """Katalog: cijena rastuće / opadajuće. Nema na stanju ide na dno."""
    if not products:
        return products

    def key(p):
        in_stock = 0 if getattr(p, 'na_stanju', False) else 1
        try:
            _bind_variation_parents(p)
            price = float(_effective_product_price(p) or 0)
        except Exception:
            price = 0.0
        name = (p.naziv or '').lower()
        return (in_stock, -price if descending else price, name)

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


SEARCH_SUGGEST_LIMIT = 8
SEARCH_SUGGEST_CANDIDATE_POOL = 24
# Max artikala za punu pretragu (Enter). RANIJE 200 je sjekao rezultate
# (admin 915 MATE, sajt samo 200). 5000 = sigurnosni strop radi CPU-a.
SEARCH_FULL_RANK_POOL = 5000
# Fine Python re-rank samo prvih N (SQL score već drži naziv gore)
SEARCH_FINE_RANK_TOP = 400
STAFF_LOOKUP_LIMIT = 25


def _suggest_product_queryset(request=None):
    """
    Lagani queryset za autocomplete — minimum polja, bez tagova M2M.
    """
    qs = Product.objects.filter(aktivan=True)
    if not _staff_edit_mode_enabled(request):
        qs = qs.filter(sakriven_do_stanja=False)
    return qs.defer(
        'opis', 'meta_title', 'meta_description',
        'olx_listing_url', 'olx_listing_slug', 'olx_listing_id',
    ).prefetch_related(
        Prefetch(
            'varijacije',
            queryset=ProductVariation.objects.filter(na_stanju=True).only(
                'id',
                'artikal_id',
                'cijena',
                'akcijska_cijena',
                'akcija_postotak',
                'na_stanju',
            ),
        ),
    )


def _suggest_thumb_url(image_field):
    """
    120w thumb URL bez storage.exists() (sporo na cloud storage-u).
    Konvencija processiranja: {base}-120w.avif / .jpg / …
    """
    if not image_field or not getattr(image_field, 'name', None):
        return ''
    name = image_field.name
    if '/' in name:
        folder, filename = name.rsplit('/', 1)
    else:
        folder, filename = '', name
    base = filename.rsplit('.', 1)[0]
    storage = image_field.storage
    # Prefer avif (glavni format u pipeline-u), pa ista ekstenzija glavne slike
    candidates = [f'{base}-120w.avif']
    if '.' in filename:
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext != 'avif':
            candidates.append(f'{base}-120w.{ext}')
    for variant in candidates:
        path = f'{folder}/{variant}' if folder else variant
        try:
            return storage.url(path)
        except Exception:
            continue
    try:
        return image_field.url
    except Exception:
        return ''


def _suggest_relevance_annotation(query):
    """
    SQL prioritet relevantnosti za brži ORDER BY (manji candidate pool).

    Prioritet (Case = prvi match pobjedi):
      1) šifra / naziv (tačno → djelomično → sve riječi)
      2) tek onda tag artikla / tag potkategorije

    Bez JOIN-a na tagovi__ (M2M) — koristi pk__in da ne duplicira redove.
    """
    raw = _normalize_phrase(query)
    if not raw or len(raw) < 2:
        return Value(0, output_field=IntegerField())
    folded = _search_fold(raw)
    terms = [raw]
    if folded and folded != raw.casefold():
        terms.append(folded)

    # Case uzima PRVI matching When — stavi više skorove prvo
    # Naziv/šifra UVJEK iznad tagova (MATE u nazivu > tag match)
    whens = []
    for term in terms:
        whens.extend([
            When(sifra__iexact=term, then=Value(120)),
            When(naziv__iexact=term, then=Value(110)),
            When(sifra__istartswith=term, then=Value(100)),
            When(naziv__istartswith=term, then=Value(95)),
        ])

    # Višerječni upit: SVE riječi u nazivu, bilo kojim redom — PRIJE fraze icontains
    words = _search_tokens(raw)
    if len(words) >= 2:
        name_all_words = Q()
        for w in words:
            name_all_words &= (
                Q(naziv__icontains=w) | Q(naziv_normalized__icontains=w)
            )
        whens.append(When(name_all_words, then=Value(93)))

    for term in terms:
        whens.extend([
            When(sifra__icontains=term, then=Value(90)),
            When(naziv__icontains=term, then=Value(88)),
            When(naziv_normalized__icontains=term, then=Value(87)),
            When(search_keywords__icontains=term, then=Value(86)),
        ])

    if len(words) >= 2:
        sifra_all_words = Q()
        for w in words:
            sifra_all_words &= Q(sifra__icontains=w)
        whens.append(When(sifra_all_words, then=Value(84)))

    # Tagovi artikla (M2M) — ispod naziva/šifre
    try:
        tag_product_ids = _product_ids_for_product_tag_query(raw)
        if tag_product_ids:
            whens.append(When(pk__in=list(tag_product_ids), then=Value(50)))
    except Exception:
        pass

    # Tag potkategorije — najniži match band (ispod naziva/šifre/taga artikla)
    try:
        exact_cat_ids = _subcategory_ids_for_exact_tag(raw)
        if folded and folded != raw.casefold():
            exact_cat_ids = exact_cat_ids | _subcategory_ids_for_exact_tag(folded)
        if exact_cat_ids:
            whens.append(When(kategorija_id__in=list(exact_cat_ids), then=Value(40)))
        else:
            fuzzy_cat_ids = _category_ids_for_search_query(raw)
            if folded and folded != raw.casefold():
                fuzzy_cat_ids = fuzzy_cat_ids | _category_ids_for_search_query(folded)
            if fuzzy_cat_ids:
                whens.append(When(kategorija_id__in=list(fuzzy_cat_ids), then=Value(30)))
    except Exception:
        pass

    return Case(*whens, default=Value(10), output_field=IntegerField())


def search_suggest(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': [], 'query': '', 'has_more': False})
    if len(_normalize_phrase(query)) < 2:
        return JsonResponse({'results': [], 'query': query, 'has_more': False})

    # SQL filter + SQL order (širi pool), pa Python re-rank: naziv/šifra > tag
    products_qs = _apply_search_filter(_suggest_product_queryset(request), query)
    products_qs = products_qs.annotate(
        _suggest_rel=_suggest_relevance_annotation(query),
    ).order_by('-_suggest_rel', '-prioritet_lagera', 'naziv')

    try:
        limit = int(request.GET.get('limit') or SEARCH_SUGGEST_LIMIT)
    except (TypeError, ValueError):
        limit = SEARCH_SUGGEST_LIMIT
    limit = max(1, min(limit, SEARCH_SUGGEST_LIMIT))
    pool_size = max(limit + 4, min(SEARCH_SUGGEST_CANDIDATE_POOL, limit * 4))
    pool = list(products_qs[:pool_size])
    # Dedup po pk (zaštita ako JOIN ikad procuri)
    seen = set()
    unique_pool = []
    for p in pool:
        if p.pk in seen:
            continue
        seen.add(p.pk)
        unique_pool.append(p)
    pool = unique_pool

    # Fini re-rank po stvarnom score-u (šifra/naziv ispred tagova)
    try:
        from django.db.models import prefetch_related_objects
        prefetch_related_objects(pool, 'varijacije', 'tagovi')
    except Exception:
        pass
    pool = sorted(
        pool,
        key=lambda p: (
            -_search_relevance_score(p, query),
            -_product_lager_priority(p),
            (p.naziv or '').lower(),
        ),
    )

    has_more = len(pool) > limit
    products = pool[:limit]

    results = []
    for product in products:
        _bind_variation_parents(product)
        price = _effective_product_price(product)
        # prikazna_slika može dirati varijacije — parent već bound
        image_field = getattr(product, 'slika', None) or product.prikazna_slika
        results.append({
            'naziv': product.naziv,
            'url': product.get_absolute_url(),
            'image': _suggest_thumb_url(image_field) if image_field else '',
            'price': f'{price:.2f}',
            'on_sale': _product_is_on_sale(product),
            'pack': product.pakovanje_label or '',
        })

    response = JsonResponse({'results': results, 'query': query, 'has_more': has_more})
    response['Cache-Control'] = 'private, max-age=60'
    return response





def _apply_product_filters(products_qs, request, *, allowed_category_ids=None):
    params = _get_filter_params(request)
    search_q = _normalize_phrase(params.get('q') or '')

    products_qs = _apply_search_filter(products_qs, params['q'])

    if params.get('akcija'):
        products_qs = _akcija_products_qs(products_qs)
    if params.get('noviteti'):
        products_qs = products_qs.filter(je_novitet=True)
    if params.get('brend'):
        brand = Brand.objects.filter(slug=params['brend']).first()
        products_qs = products_qs.filter(brend_id=brand.pk) if brand else products_qs.none()
    if params.get('kategorija'):
        category = Category.objects.filter(slug=params['kategorija'], aktivan=True).first()
        if category:
            products_qs = products_qs.filter(kategorija_id__in=category.get_descendant_ids())
        else:
            products_qs = products_qs.none()
    if allowed_category_ids is not None:
        products_qs = products_qs.filter(kategorija_id__in=allowed_category_ids)

    # SQL pre-order za pretragu — naziv/šifra gore; NE sijeći na 200 (fali ostatak)
    if search_q and len(search_q) >= 2:
        products_qs = products_qs.annotate(
            _search_sql_rel=_suggest_relevance_annotation(search_q),
        ).order_by('-_search_sql_rel', '-prioritet_lagera', 'naziv')
        products = list(products_qs[:SEARCH_FULL_RANK_POOL])
    else:
        products = list(products_qs)

    # Zaštita od duplikata (isti pk više puta u listi)
    if products:
        seen_pks = set()
        unique_products = []
        for product in products:
            pk = getattr(product, 'pk', None)
            if pk in seen_pks:
                continue
            if pk is not None:
                seen_pks.add(pk)
            unique_products.append(product)
        products = unique_products

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
    if price_min is not None or price_max is not None:
        for product in products:
            _bind_variation_parents(product)
        if price_min is not None:
            products = [product for product in products if _effective_product_price(product) >= price_min]
        if price_max is not None:
            products = [product for product in products if _effective_product_price(product) <= price_max]

    if params['akcija']:
        for product in products:
            _bind_variation_parents(product)
        products = [product for product in products if _product_is_on_sale(product)]

    if params['noviteti']:
        products = [product for product in products if getattr(product, 'je_novitet', False)]

    if params['velicina']:
        size_label = params['velicina']
        products = [
            product for product in products
            if _product_matches_size(product, size_label)
        ]

    sort = (params.get('sort') or '').strip()
    if sort in ('rastuca', 'opadajuca'):
        products = _sort_products_by_price(
            products,
            descending=(sort == 'opadajuca'),
        )
    elif search_q and len(search_q) >= 2 and len(products) > SEARCH_FINE_RANK_TOP:
        head = _sort_products_by_lager_priority(
            products[:SEARCH_FINE_RANK_TOP],
            query=search_q,
            price_sort='rastuca',
        )
        products = head + products[SEARCH_FINE_RANK_TOP:]
    else:
        products = _sort_products_by_lager_priority(
            products,
            query=search_q,
            price_sort='rastuca',
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


def _catalog_page_numbers(page_obj, *, around=2):
    """Brojevi stranica: uvijek 1 i zadnja, plus susjedi trenutne."""
    if not page_obj:
        return []
    last = page_obj.paginator.num_pages
    current = page_obj.number
    if last <= 1:
        return [1] if last == 1 else []
    keep = {1, last, current}
    for n in range(current - around, current + around + 1):
        if 1 <= n <= last:
            keep.add(n)
    ordered = sorted(keep)
    out = []
    prev = None
    for n in ordered:
        if prev is not None and n > prev + 1:
            out.append('…')
        out.append(n)
        prev = n
    return out


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
    """Isti normalizator putanje kao Banner.get_link_href (bez price filtera)."""
    if not link:
        return None
    raw = (link or '').strip()
    if not raw:
        return None
    if raw.startswith(('http://', 'https://')):
        from urllib.parse import urlparse
        from django.conf import settings as dj_settings
        parsed = urlparse(raw)
        host = (parsed.hostname or '').lower()
        site_host = ''
        try:
            site_host = (urlparse(getattr(dj_settings, 'SITE_URL', '') or '').hostname or '').lower()
        except Exception:
            pass
        local_hosts = {
            'localhost', '127.0.0.1', '0.0.0.0',
            'www.opremazaribolov.ba', 'opremazaribolov.ba',
        }
        if site_host:
            local_hosts.add(site_host)
            if site_host.startswith('www.'):
                local_hosts.add(site_host[4:])
            else:
                local_hosts.add(f'www.{site_host}')
        if host in local_hosts or host.endswith('.onrender.com'):
            path = parsed.path or '/'
            if parsed.query:
                path = f'{path}?{parsed.query}'
            if parsed.fragment:
                path = f'{path}#{parsed.fragment}'
            return path
        return raw
    if raw.startswith('/'):
        return raw
    if raw.startswith('?'):
        return f'/{raw}'
    return f'/{raw.strip("/")}/'


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


def _banner_media_meta(banner, *, tip='hero', default=(1920, 640)):
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
    media = _banner_media_meta(banner, tip='hero', default=(1920, 640))
    mobile = {
        'image_mobile': '',
        'image_mobile_srcset': '',
        'image_mobile_width': 720,
        'image_mobile_height': 900,
        'has_mobile_image': False,
    }
    if getattr(banner, 'slika_mobilna', None):
        from .utils.images import banner_image_responsive_meta
        m = banner_image_responsive_meta(
            banner.slika_mobilna,
            tip='hero_mobile',
            default=(720, 900),
        )
        mobile = {
            'image_mobile': m['src'],
            'image_mobile_srcset': m.get('srcset') or '',
            'image_mobile_width': m.get('width') or 720,
            'image_mobile_height': m.get('height') or 900,
            'has_mobile_image': bool(m.get('src')),
        }
    return {
        'title': banner.naslov,
        'subtitle': banner.podnaslov,
        'url': banner.get_link_href(),
        'actions': _banner_actions(banner),
        **media,
        **mobile,
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
HOME_SECTION_PRODUCT_VISIBLE = 5
HOME_SECTION_PRODUCT_VISIBLE_MOBILE = 2
HOME_CATEGORY_SHOWCASE_LIMIT = 6
HOME_VLOG_LIMIT = 3
HOME_CACHE_TTL = 180


def _home_cache_get(key, factory, ttl=HOME_CACHE_TTL):
    from django.core.cache import cache

    cached = cache.get(key)
    if cached is not None:
        return cached
    value = factory()
    try:
        cache.set(key, value, ttl)
    except Exception:
        logger.exception('Home cache set failed: %s', key)
    return value


def _fill_home_section_products(products, request=None):
    """Dopuni sekciju do HOME_SECTION_PRODUCT_LIMIT da karusel ima 5 u nizu."""
    items = list(products or [])
    if len(items) >= HOME_SECTION_PRODUCT_LIMIT:
        return items[:HOME_SECTION_PRODUCT_LIMIT]
    seen = {p.pk for p in items}
    extra_qs = _product_queryset(request)
    if seen:
        extra_qs = extra_qs.exclude(pk__in=seen)
    extra_qs = _order_qs_by_lager_priority(extra_qs, '-kreiran', '-id')[
        : HOME_SECTION_PRODUCT_LIMIT - len(items)
    ]
    items.extend(extra_qs)
    return items


def _home_latest_products(request=None):
    """
    Noviteti na početnoj:
    1) Artikli označeni „Noviteti” (je_novitet) — prioritet
    2) Ručni odabir (HomeNovoProduct) ako je mod manual
    3) Dopuna: najnoviji artikli da bude 5 u nizu
    """
    return _home_cache_get(
        'home_latest_products_v3',
        lambda: _home_latest_products_uncached(request),
    )


def _home_latest_products_uncached(request=None):
    base_qs = _product_queryset(request)
    marked = list(
        _order_qs_by_lager_priority(
            base_qs.filter(je_novitet=True),
            '-kreiran', '-id',
        )[:HOME_SECTION_PRODUCT_LIMIT],
    )
    if len(marked) >= HOME_SECTION_PRODUCT_LIMIT:
        return marked

    if not marked:
        site_settings = SiteSettings.load()
        if site_settings.noviteti_mod == SiteSettings.NovitetiMod.MANUAL:
            entries_qs = HomeNovoProduct.objects.filter(
                aktivan=True,
                artikal__aktivan=True,
            )
            entries = entries_qs.select_related(
                'artikal', 'artikal__kategorija', 'artikal__brend',
            ).prefetch_related(
                Prefetch('artikal__varijacije', queryset=_in_stock_variations_qs()),
            ).order_by(
                '-artikal__prioritet_lagera', 'redoslijed', '-id',
            )[:HOME_SECTION_PRODUCT_LIMIT]
            marked = [entry.artikal for entry in entries]

    return _fill_home_section_products(marked, request)


def _home_featured_products(request=None):
    """
    Izdvojeni na početnoj:
    1) Artikli označeni „HIT / Izdvojeno” (je_hit)
    2) Fallback: ručni HomeFeaturedProduct
    Među njima: redukovanje lagera ima prednost.
    """
    return _home_cache_get(
        'home_featured_products_v3',
        lambda: _home_featured_products_uncached(request),
    )


def _home_featured_products_uncached(request=None):
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
    entries = entries_qs.select_related(
        'artikal', 'artikal__kategorija', 'artikal__brend',
    ).prefetch_related(
        Prefetch('artikal__varijacije', queryset=_in_stock_variations_qs()),
    ).order_by(
        '-artikal__prioritet_lagera', 'redoslijed', '-id',
    )[:HOME_SECTION_PRODUCT_LIMIT]
    return [entry.artikal for entry in entries]


def _home_sale_products(request=None):
    """Akcijska ponuda na početnoj — artikli sa sniženom cijenom."""
    return _home_cache_get(
        'home_sale_products_v3',
        lambda: _home_sale_products_uncached(request),
    )


def _home_sale_products_uncached(request=None):
    base_qs = _product_queryset(request)
    sale_qs = _akcija_products_qs(base_qs)
    return list(
        _order_qs_by_lager_priority(sale_qs, '-kreiran', '-id')[:HOME_SECTION_PRODUCT_LIMIT],
    )


def _home_trust_items():
    """Samo aktivne stavke s naslovom — bez praznih / auto redova."""
    from django.core.cache import cache

    cache_key = 'home_trust_items_v1'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        rows = list(
            HomeTrustItem.objects.filter(aktivan=True)
            .exclude(naslov='')
            .order_by('redoslijed', 'id')[:6],
        )
    except DatabaseError:
        rows = []
    cache.set(cache_key, rows, 180)
    return rows


def _home_promo_cards():
    from django.core.cache import cache

    cache_key = 'home_promo_cards_v1'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        rows = list(
            HomePromoCard.objects.filter(aktivan=True).order_by('redoslijed', 'id')[:8],
        )
    except DatabaseError:
        rows = []
    cache.set(cache_key, rows, 180)
    return rows


def _home_category_showcases(request=None):
    return _home_cache_get(
        f'home_cat_show_v4:{HOME_CATEGORY_SHOWCASE_LIMIT}',
        lambda: _home_category_showcases_uncached(request),
    )


def _home_category_showcases_uncached(request=None):
    entries = HomeCategoryShowcase.objects.filter(
        aktivan=True,
        kategorija__aktivan=True,
    ).select_related('kategorija').prefetch_related(
        'kategorija__podkategorije__podkategorije',
    ).order_by('redoslijed', 'id')

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
    Brend sekcije na početnoj — karusel do 10 artikala po brendu (vrte se).
    Admin: Postavke sajta → ⑥ Brend karuseli.
    """
    return _home_cache_get(
        'home_brand_show_v3',
        lambda: _home_brand_showcases_uncached(request),
    )


def _home_brand_showcases_uncached(request=None):
    entries = HomeBrandShowcase.objects.filter(
        aktivan=True,
    ).select_related('brend').order_by('redoslijed', 'id')

    home_url = reverse('home')
    # Strogo max 10 artikala u karuselu
    brand_limit = min(10, HOME_SECTION_PRODUCT_LIMIT)
    sections = []
    for entry in entries:
        products = list(
            _order_qs_by_lager_priority(
                _product_queryset(request).filter(brend_id=entry.brend_id),
                '-kreiran',
            )[:brand_limit],
        )
        if not products:
            continue
        brand = entry.brend
        brand_url = f'{home_url}?brend={brand.slug}#product-showcase'
        sections.append({
            'title': entry.display_title(),
            'brand': brand,
            'brand_url': brand_url,
            'products': products[:10],
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

        image_meta = vlog_image_responsive_meta(vlog.slika, default=(800, 500))
        display_date = getattr(vlog, 'display_date', None)
        teaser = ''
        if hasattr(vlog, 'short_teaser'):
            teaser = vlog.short_teaser()
        vlogs.append({
            'id': vlog.pk,
            'slug': vlog.slug,
            'naslov': vlog.naslov,
            'teaser': teaser,
            'datum': display_date,
            'slika_url': image_meta['src'],
            'slika_srcset': image_meta['srcset'],
            'image_width': image_meta['width'],
            'image_height': image_meta['height'],
        })
    return vlogs


def _home_vlogs():
    from django.core.cache import cache

    cache_key = f'home_vlogs_v2:{HOME_VLOG_LIMIT}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    rows = _vlog_cards(HOME_VLOG_LIMIT)
    cache.set(cache_key, rows, 180)
    return rows


def _vlog_seo_description(sadrzaj, max_len=160):
    text = strip_tags(sadrzaj).strip()
    if len(text) <= max_len:
        return text
    trimmed = text[:max_len - 1]
    if ' ' in trimmed:
        trimmed = trimmed.rsplit(' ', 1)[0]
    return f'{trimmed}…'


@require_POST
def newsletter_subscribe(request):
    """AJAX pretplata na newsletter sa početne stranice."""
    email = (request.POST.get('email') or '').strip().lower()
    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return JsonResponse(
            {'ok': False, 'message': 'Unesite ispravnu e-mail adresu.'},
            status=400,
        )
    if len(email) > 254:
        return JsonResponse(
            {'ok': False, 'message': 'E-mail adresa je preduga.'},
            status=400,
        )
    try:
        sub, created = MarketingSubscriber.objects.get_or_create(
            email=email,
            defaults={
                'izvor': MarketingSubscriber.Source.MANUAL,
                'aktivan': True,
            },
        )
        if not created and not sub.aktivan:
            sub.aktivan = True
            sub.save(update_fields=['aktivan'])
            created = True
    except DatabaseError:
        logger.exception('Newsletter pretplata nije uspjela')
        return JsonResponse(
            {'ok': False, 'message': 'Greška pri prijavi. Pokušajte ponovo.'},
            status=500,
        )
    if created:
        return JsonResponse({
            'ok': True,
            'message': 'Uspješno ste se prijavili na newsletter!',
        })
    return JsonResponse({
        'ok': True,
        'message': 'Već ste prijavljeni na newsletter. Hvala!',
    })


def home(request):
    hero_banners = _banners_with_media(Banner.objects.filter(
        tip=Banner.BannerType.HERO, aktivan=True,
    ).order_by('redoslijed', '-id'))
    grid_banners = _filter_banners_for_empty_categories(
        _banners_with_media(Banner.objects.filter(
            tip=Banner.BannerType.GRID, aktivan=True,
        ).select_related('kategorija').order_by('redoslijed', '-id'))[:3]
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
    sale_products = []
    home_trust_items = []
    home_promo_cards = []
    home_category_showcases = []
    home_brand_showcases = []
    home_vlogs = []
    page_obj = None
    search_products = []
    catalog_title = None
    catalog_subtitle = None
    filter_size_groups = []
    home_url = reverse('home')
    site_settings = SiteSettings.load()

    if filters_active:
        products, filter_params = _apply_product_filters(_product_queryset(request), request)
        scope_qs = _filter_size_scope_qs(filter_params, request=request)
        filter_sizes = _available_sizes(scope_qs)
        filter_size_groups = _size_filter_groups(home_url, filter_params, filter_sizes)
        page_obj = _paginate_catalog_products(request, products)
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
        if site_settings.prikazi_akcijsku_sekciju:
            sale_products = _home_sale_products(request)
        home_trust_items = _home_trust_items()
        home_promo_cards = []
        home_category_showcases = _home_category_showcases(request)
        home_brand_showcases = _home_brand_showcases(request)
        home_vlogs = _home_vlogs()

    # Evaluiraj banere jednom (izbjegni .exists() + .first() = 2× query)
    hero_banners_list = list(hero_banners)
    first_hero = hero_banners_list[0] if hero_banners_list else None
    first_grid_banner = grid_banners[0] if grid_banners else None
    has_hero_slides = bool(not filters_active and hero_banners_list)
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
                default=(1920, 640),
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
        elif latest_products or featured_products or sale_products:
            first_product = (latest_products or featured_products or sale_products)[0]
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
        'hero_slides': [_banner_to_hero_slide(b) for b in hero_banners_list],
        'grid_banners': [_banner_to_card(b) for b in grid_banners],
        'featured_cards': [_banner_to_card(b) for b in featured_banners],
        'spotlight': spotlight,
        'latest_products': latest_products,
        'featured_products': featured_products,
        'sale_products': sale_products,
        'home_trust_items': home_trust_items,
        'home_promo_cards': home_promo_cards,
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
        'filter_reset_url': _filter_reset_url(home_url, filter_params),
        'catalog_title': catalog_title,
        'catalog_subtitle': catalog_subtitle,
        'catalog_query': _catalog_query_string(filter_params),
        'elided_page_range': _catalog_page_numbers(page_obj),
        'selected_brand': Brand.objects.filter(slug=filter_params['brend']).first() if filter_params.get('brend') else None,
        'home_section_product_visible': HOME_SECTION_PRODUCT_VISIBLE,
        'home_section_product_visible_mobile': HOME_SECTION_PRODUCT_VISIBLE_MOBILE,
        'canonical_url': settings.SITE_URL.rstrip('/') + '/',
    }
    # SEO: početna ili filtrirani katalog (akcija / noviteti / pretraga / brend)
    selected_brand = context['selected_brand']
    if filters_active:
        if filter_params.get('akcija'):
            context.update(page_seo_context('akcija', defaults={
                'seo_title': 'Akcija | Oprema za ribolov',
                'seo_description': 'Artikli na sniženoj cijeni — opremazaribolov.ba',
                'seo_h1': catalog_title or 'Akcija',
            }))
        elif filter_params.get('noviteti'):
            context.update(page_seo_context('noviteti', defaults={
                'seo_title': 'Noviteti | Oprema za ribolov',
                'seo_description': 'Novi artikli u ponudi — opremazaribolov.ba',
                'seo_h1': catalog_title or 'Noviteti',
            }))
        elif filter_params.get('q'):
            context.update(page_seo_context('search', defaults={
                'seo_title': f'Pretraga: {filter_params["q"]} | Oprema za ribolov',
                'seo_description': f'Rezultati pretrage za „{filter_params["q"]}".',
                'seo_h1': catalog_title or 'Rezultati pretrage',
            }))
        elif selected_brand:
            context.update(entity_seo_context(
                meta_title=selected_brand.meta_title,
                meta_description=selected_brand.meta_description,
                h1_naslov=selected_brand.h1_naslov,
                seo_tekst_iznad=selected_brand.seo_tekst_iznad,
                seo_tekst_ispod=selected_brand.seo_tekst_ispod,
                default_title=f'{selected_brand.naziv} | Oprema za ribolov',
                default_description=(
                    f'{selected_brand.naziv} — kvalitetna oprema za ribolov. '
                    f'Brza dostava širom BiH.'
                ),
                default_h1=selected_brand.naziv,
            ))
        else:
            context.update(page_seo_context('search', defaults={
                'seo_title': f'{catalog_title or "Rezultati"} | Oprema za ribolov',
                'seo_description': catalog_subtitle or '',
                'seo_h1': catalog_title or 'Rezultati',
            }))
        # Ako SEO H1 postoji, prepiši catalog_title da header prikaže H1
        if context.get('seo_h1'):
            context['catalog_title'] = context['seo_h1']
    else:
        context.update(page_seo_context('home', defaults={
            'seo_title': site_settings.seo_title or '',
            'seo_description': site_settings.meta_description or '',
            'seo_h1': '',
        }))
        context['seo_h1'] = ''
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

    vlog_image = vlog_image_responsive_meta(vlog.slika, default=(800, 500))
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
        **page_seo_context('about', defaults={
            'seo_title': 'O nama — opremazaribolov.ba',
            'seo_description': (
                'Saznajte više o opremazaribolov.ba — dugogodišnje iskustvo u ribolovu '
                'i opremi, sada u online prodaji za ribare u Bosni i Hercegovini.'
            ),
            'seo_h1': 'O nama',
        }),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('about_us'),
    }
    return render(request, 'pages/about.html', context)


def payment_methods(request):
    context = {
        **_base_context(),
        **page_seo_context('payment', defaults={
            'seo_title': 'Način plaćanja — opremazaribolov.ba',
            'seo_description': (
                'Plaćanje prilikom preuzimanja, dostava brzom poštom u roku 48h i sigurno slanje pošiljki.'
            ),
            'seo_h1': 'Način plaćanja',
        }),
        'canonical_url': settings.SITE_URL.rstrip('/') + reverse('payment_methods'),
    }
    return render(request, 'pages/payment.html', context)


def brands_list(request):
    brands_qs = Brand.objects.filter(
        id__in=(
            Product.objects.filter(aktivan=True, sakriven_do_stanja=False)
            .exclude(brend_id__isnull=True)
            .values_list('brend_id', flat=True)
            .distinct()
        )
    )
    sort = (request.GET.get('sort') or 'az').strip().lower()
    if sort == 'za':
        brands_qs = brands_qs.order_by('-naziv')
    else:
        sort = 'az'
        brands_qs = brands_qs.order_by('naziv')

    page_obj = _paginate_catalog_products(request, brands_qs, per_page=24)
    total = page_obj.paginator.count
    start = page_obj.start_index() if total else 0
    end = page_obj.end_index() if total else 0
    canonical = settings.SITE_URL.rstrip('/') + reverse('brands_list')
    context = {
        **_base_context(),
        'brands': page_obj.object_list,
        'page_obj': page_obj,
        'elided_page_range': _catalog_page_numbers(page_obj),
        'brands_sort': sort,
        'brands_shown_start': start,
        'brands_shown_end': end,
        'brands_total': total,
        **page_seo_context('brands', defaults={
            'seo_title': 'Brendovi — opremazaribolov.ba',
            'seo_description': (
                'Svi brendovi ribolovne opreme na opremazaribolov.ba — Fox, Shimano, '
                'Daiwa, Korda i drugi. Pronađite vrhunsku opremu poznatih brendova.'
            ),
            'seo_h1': 'Svi brendovi',
        }),
        'canonical_url': canonical,
        'breadcrumb_json_ld': json_ld(breadcrumb_json_ld([
            {'name': 'Početna', 'url': settings.SITE_URL.rstrip('/') + '/'},
            {'name': 'Brendovi', 'url': canonical},
        ])),
    }
    return render(request, 'brands.html', context)


def vlog_list(request):
    context = {
        **_base_context(),
        'vlogs': _vlog_cards(),
        **page_seo_context('vlog', defaults={
            'seo_title': 'Blog — opremazaribolov.ba',
            'seo_description': (
                'Blog i vlog opremazaribolov.ba — savjeti, priče i novosti iz svijeta ribolova.'
            ),
            'seo_h1': 'Blog',
        }),
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

    category_seo = entity_seo_context(
        meta_title=category.meta_title,
        meta_description=category.meta_description,
        h1_naslov=category.h1_naslov,
        seo_tekst_iznad=category.seo_tekst_iznad,
        seo_tekst_ispod=category.seo_tekst_ispod,
        default_title=auto_category_seo_title(category),
        default_description=auto_category_seo_description(category),
        default_h1=category.naziv,
    )
    cat_canonical = settings.SITE_URL.rstrip('/') + category.get_absolute_url()
    category_ld = {
        'collection_json_ld': json_ld(collection_page_json_ld(
            name=category_seo['seo_h1'] or category.naziv,
            description=category_seo['seo_description'],
            url=cat_canonical,
        )),
        'breadcrumb_json_ld': json_ld(breadcrumb_json_ld([
            {'name': 'Početna', 'url': settings.SITE_URL.rstrip('/') + '/'},
            {'name': category.naziv, 'url': cat_canonical},
        ])),
    }

    if direct_subs and not show_products:
        context = {
            **_base_context(),
            'category': category,
            'subcategories': direct_subs,
            **category_seo,
            'seo_description': (
                category.meta_description
                or f'Izaberite podkategoriju unutar {category.naziv}. {auto_category_seo_description(category)}'
            )[:160],
            'canonical_url': cat_canonical,
            **category_ld,
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
        'elided_page_range': _catalog_page_numbers(page_obj),
        'catalog_query': _catalog_query_string(catalog_url_params),
        'filter_categories': _filter_categories(),
        'filter_params': filter_params,
        'filter_size_groups': _size_filter_groups(category_url, catalog_url_params, filter_sizes),
        'filter_reset_url': _filter_reset_url(category_url, catalog_url_params),
        'category_subnav': _category_subnav_items(category, show_all_active=show_all),
        'catalog_show_all': show_all,
        **category_seo,
        'canonical_url': cat_canonical,
        **category_ld,
    }
    return render(request, 'category.html', context)


def _safe_internal_path(url, request=None):
    """
    Dozvoli samo interni path (open-redirect safe).
    Vraća '/putanja?query' ili ''.
    """
    url = (url or '').strip()
    if not url:
        return ''
    # već relativan path
    if url.startswith('/') and not url.startswith('//'):
        # blokiraj protocol-relative i javascript
        if url.lower().startswith('/\\') or '\n' in url or '\r' in url:
            return ''
        return url
    try:
        parsed = urlparse(url)
    except Exception:
        return ''
    if parsed.scheme and parsed.scheme not in ('http', 'https'):
        return ''
    host = ''
    if request is not None:
        host = (request.get_host() or '').split(':')[0].lower()
    netloc = (parsed.netloc or '').split(':')[0].lower()
    if netloc and host and netloc != host and not netloc.endswith('.' + host):
        return ''
    if netloc and not host:
        # bez requesta — samo relativni
        return ''
    path = parsed.path or '/'
    if parsed.query:
        path = f'{path}?{parsed.query}'
    if not path.startswith('/'):
        path = '/' + path
    return path


def _product_back_url(request, product):
    """
    Stranica s koje je kupac/staff došao na artikal (katalog, pretraga…).
    Preferira ?next=, zatim Referer, pa kategoriju / početnu.
    """
    product_path = product.get_absolute_url()

    next_q = _safe_internal_path(request.GET.get('next') or '', request)
    if next_q and product_path not in next_q.split('?')[0]:
        return next_q

    referer = request.META.get('HTTP_REFERER', '')
    ref_path = _safe_internal_path(referer, request)
    if ref_path:
        ref_base = ref_path.split('?')[0].rstrip('/')
        prod_base = product_path.rstrip('/')
        if ref_base != prod_base and product_path not in ref_base:
            return ref_path

    # Sesija: zapamti zadnji „van artikla” URL u edit modu
    session_back = _safe_internal_path(
        request.session.get('staff_product_return_url') or '',
        request,
    )
    if session_back and product_path not in session_back.split('?')[0]:
        return session_back

    if product.kategorija_id:
        return product.kategorija.get_absolute_url()
    return reverse('home')


def _staff_product_edit_redirect(request, slug, *, stay_on_error=False):
    """
    Nakon uspješnog staff save-a: vrati na prethodnu listu (next),
    inače ostani na artiklu.
    """
    next_url = _safe_internal_path(request.POST.get('next') or '', request)
    product_path = reverse('product_detail', kwargs={'slug': slug})
    if next_url:
        next_base = next_url.split('?')[0].rstrip('/')
        if next_base != product_path.rstrip('/') and product_path not in next_base:
            return redirect(next_url)
    return redirect('product_detail', slug=slug)


def product_detail(request, slug):
    # Edit mode ON → superuser vidi i neaktivne.
    # Kupci: aktivni artikli i kad nisu na stanju (stranica ostaje, bez korpe).
    # Neaktivni (aktivan=False) → 404 za kupce.
    if _can_view_out_of_stock(request):
        product_qs = Product.objects.all()
    else:
        product_qs = Product.objects.filter(aktivan=True, sakriven_do_stanja=False)
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
    product_available = bool(product.na_stanju or in_stock_variations)
    lcp_image_url = None
    product_image_width, product_image_height = 800, 800
    if product.prikazna_slika:
        product_image_width, product_image_height = image_field_dimensions(
            product.prikazna_slika, default=(800, 800),
        )
        lcp_image_url = request.build_absolute_uri(product.prikazna_slika.url)

    related_products = _related_category_products(product, request=request)
    similar_name_products = find_similar_name_products(product, _product_queryset(request))
    site_settings = SiteSettings.load()
    kategorija_naziv = product.kategorija.naziv if product.kategorija else ''

    context = {
        **_base_context(),
        'product': product,
        'in_stock_variations': in_stock_variations,
        'ima_varijacije': bool(in_stock_variations),
        'product_available': product_available,
        'related_products': related_products,
        'similar_name_products': similar_name_products,
        'povezani_podnaslov': site_settings.format_povezani_podnaslov(kategorija_naziv),
        'lcp_image_url': lcp_image_url,
        'product_image_width': product_image_width,
        'product_image_height': product_image_height,
        # SEO
        **entity_seo_context(
            meta_title=product.meta_title,
            meta_description=product.meta_description,
            h1_naslov=product.h1_naslov,
            seo_tekst_iznad=product.seo_tekst_iznad,
            seo_tekst_ispod=product.seo_tekst_ispod,
            default_title=product.seo_title,
            default_description=product.seo_description,
            default_h1=product.naziv,
        ),
        'canonical_url': settings.SITE_URL.rstrip('/') + product.get_absolute_url(),
        'og_image': (
            request.build_absolute_uri(product.prikazna_slika.url)
            if product.prikazna_slika else None
        ),
        'product_back_url': _product_back_url(request, product),
        # Van stanja: ne indeksiraj (i dalje otvoren link za stare bookmarke)
        'meta_robots_content': None if product_available else 'noindex, follow',
        'product_json_ld': json_ld(product_json_ld(
            product,
            canonical_url=settings.SITE_URL.rstrip('/') + product.get_absolute_url(),
            site_settings=site_settings,
            request=request,
        )),
        'breadcrumb_json_ld': json_ld(breadcrumb_json_ld([
            {'name': 'Početna', 'url': settings.SITE_URL.rstrip('/') + '/'},
            *(
                [{
                    'name': product.kategorija.naziv,
                    'url': settings.SITE_URL.rstrip('/') + product.kategorija.get_absolute_url(),
                }]
                if product.kategorija_id else []
            ),
            {
                'name': product.naziv,
                'url': settings.SITE_URL.rstrip('/') + product.get_absolute_url(),
            },
        ])),
    }
    # Zapamti povratak za staff edit (Save → nazad na listu/pretragu)
    back = context['product_back_url']
    back_path = _safe_internal_path(back, request) or back
    context['staff_edit_return_url'] = back_path
    if (
        _staff_edit_mode_enabled(request)
        and back_path
        and product.get_absolute_url() not in (back_path.split('?')[0] or '')
    ):
        request.session['staff_product_return_url'] = back_path
        request.session.modified = True

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

    from .gratis import get_active_qty_deal_for_product
    qty_deal_akcija = get_active_qty_deal_for_product(product)
    if qty_deal_akcija:
        context['qty_deal_page'] = qty_deal_akcija.qty_deal_page_offer()

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
    context['product_bundle'] = _product_page_bundle(product)
    context['flash_offer'] = _product_page_flash_offer(product)

    return render(request, 'product_detail.html', context)


def _product_page_bundle(product):
    """Aktivni bundle za ovu stranicu artikla.

    Prikazuje se ako je artikal u setu, ili ako je trigger kategorija
    i artikal pripada toj kategoriji (uključujući potkategorije).
    """
    from .models import Akcija, AkcijaBundleLine

    akcije = (
        Akcija.objects.filter(aktivan=True, tip=Akcija.Tip.BUNDLE)
        .filter(
            Q(bundle_lines__product=product)
            | Q(bundle_artikli=product)
            | Q(
                bundle_trigger=Akcija.BundleTrigger.CATEGORY,
                kategorija_id__isnull=False,
            )
        )
        .select_related('kategorija', 'kategorija__roditelj')
        .prefetch_related(
            Prefetch(
                'bundle_lines',
                queryset=AkcijaBundleLine.objects.select_related(
                    'product', 'product__kategorija',
                ).order_by('redoslijed', 'id'),
            ),
            'bundle_artikli',
        )
        .distinct()
        .order_by('redoslijed', '-id')
    )
    product_category = getattr(product, 'kategorija', None)
    chosen = None
    category_fallback = None
    for akcija in akcije:
        if not akcija.jos_traje():
            continue
        items = akcija.bundle_display_items()
        pricing = akcija.bundle_pricing_summary()
        if not items or len(items) < 2 or not pricing:
            continue
        in_set = any(
            item.get('product') is not None and item['product'].pk == product.pk
            for item in items
        )
        trigger = (akcija.bundle_trigger or '').strip()
        category_ok = (
            trigger == Akcija.BundleTrigger.CATEGORY
            and akcija.kategorija_id
            and product_category is not None
            and akcija._category_matches_root(product_category, akcija.kategorija_id)
        )
        if not in_set and not category_ok:
            continue
        pack = (akcija, items, pricing)
        if in_set:
            chosen = pack
            break
        if category_fallback is None:
            category_fallback = pack
    chosen = chosen or category_fallback
    if not chosen:
        return None
    akcija, items, pricing = chosen
    price_parts = ' + '.join(
        f'{item["bazna"]:.2f} KM' if item.get('bazna') is not None else '—'
        for item in items
    )
    return {
        'akcija': akcija,
        'items': items,
        'pricing': pricing,
        'price_parts': price_parts,
    }


def _product_page_flash_offer(product):
    """Akcijska ponuda za ovu stranicu artikla (nije popup)."""
    from .models import Akcija, AkcijaFlashLine

    akcije = (
        Akcija.objects.filter(aktivan=True, tip=Akcija.Tip.AKCIJSKA)
        .select_related('kategorija', 'kategorija__roditelj', 'artikal')
        .prefetch_related(
            Prefetch(
                'flash_lines',
                queryset=AkcijaFlashLine.objects.select_related(
                    'product', 'product__kategorija',
                ).prefetch_related('product__varijacije').order_by('redoslijed', 'id'),
            ),
        )
        .order_by('redoslijed', '-id')
    )
    for akcija in akcije:
        if not akcija.flash_applies_to_product(product):
            continue
        rows = akcija.flash_line_rows()
        related = []
        for line in rows:
            p = line.product
            pct = line.effective_discount_percent(akcija)
            pricing = akcija.flash_item_pricing(p, pct)
            if not pricing:
                continue
            related.append({
                'product': p,
                'pricing': pricing,
                'in_stock': bool(
                    p.na_stanju
                    or p.varijacije.filter(na_stanju=True).exists()
                ),
            })
        remaining = akcija.flash_remaining_seconds()
        hours_left = max(1, (remaining + 3599) // 3600) if remaining else (akcija.trajanje_sati or 0)
        end = akcija.zavrsava
        return {
            'akcija': akcija,
            'related': related[:4],
            'remaining_seconds': remaining,
            'expires_ts': int(end.timestamp()) if end else 0,
            'hours_left': hours_left,
            'naslov': (akcija.flash_naslov or '').strip() or 'POSEBNA PONUDA – SAMO SADA!',
            'podnaslov': (akcija.flash_podnaslov or '').strip(),
        }
    return None


@require_POST
def add_to_cart(request, slug):
    # Fetch product allowing sold-out (we validate stock below)
    product = get_object_or_404(
        Product.objects.filter(aktivan=True, sakriven_do_stanja=False).select_related('kategorija'),
        slug=slug,
    )
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
    is_bundle_add = bool(
        timer_akcija_from_popup
        and Akcija.objects.filter(
            pk=timer_akcija_from_popup,
            aktivan=True,
            tip=Akcija.Tip.BUNDLE,
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
            elif timer_akcija_from_popup or request.POST.get('flash_offer_id'):
                # Bundle / akcijska ponuda: varijanta triggera nije obavezna.
                variation = in_stock.first() if in_stock.exists() else None
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

    if not is_bundle_add:
        on_hand = stock_on_hand(product, variation)
        remaining = cart.remaining_stock(product, variation)
        if remaining <= 0 or quantity > remaining:
            msg = stock_limit_message(on_hand)
            if stay_on_page:
                return JsonResponse({'ok': False, 'message': msg}, status=400)
            messages.error(request, msg)
            return redirect('product_detail', slug=slug)

    custom_price = None
    promo_bazna = None
    promo_akcija = None
    exit_popup_percent = None
    is_exit_popup_add = request.POST.get('exit_popup') == '1'

    # AI dwell flash cijena (2 min snizenje na product page, bez popupa).
    # Ne primjenjuj na exit-popup add — exit ima svoj % (0 = redovna cijena).
    if not is_exit_popup_add:
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

    # Akcijska ponuda — cijena sa stranice artikla / vezanih kartica
    if custom_price is None and not is_exit_popup_add:
        try:
            from .models import Akcija as _Akcija, _izracunaj_akcijsku_od_postotka
            flash_id = (request.POST.get('flash_offer_id') or '').strip()
            flash_akcija = None
            if flash_id:
                flash_akcija = (
                    _Akcija.objects.filter(
                        pk=flash_id, aktivan=True, tip=_Akcija.Tip.AKCIJSKA,
                    )
                    .prefetch_related('flash_lines')
                    .first()
                )
                if flash_akcija and not flash_akcija.flash_applies_to_product(product):
                    # Vezani artikal iz ponude: dozvoli i ako trigger nije ovaj artikal
                    in_offer = any(
                        getattr(ln, 'product_id', None) == product.pk
                        for ln in flash_akcija.flash_line_rows()
                    )
                    if not in_offer:
                        flash_akcija = None
            if flash_akcija and flash_akcija.flash_still_running():
                pct = flash_akcija.popust_postotak
                for ln in flash_akcija.flash_line_rows():
                    if ln.product_id == product.pk:
                        pct = ln.effective_discount_percent(flash_akcija)
                        break
                base = variation.prikazna_cijena if variation else product.bazna_cijena
                sale = _izracunaj_akcijsku_od_postotka(base, pct)
                if sale is not None:
                    custom_price = sale
                    promo_bazna = base
                    promo_akcija = flash_akcija
                    request._flash_discount_percent = pct
        except Exception:
            pass

    if is_exit_popup_add:
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
        # 0% / bez popusta → custom_price ostaje None (redovna cijena)
        if exit_popup_add.get('custom_price') is not None:
            custom_price = exit_popup_add['custom_price']
            promo_bazna = exit_popup_add['promo_bazna']
        else:
            custom_price = None
            promo_bazna = None
        exit_popup_percent = exit_popup_add.get('percent')
    from .gratis import (
        _add_discounted_gratis_line,
        apply_gratis_bundle_from_popup,
        apply_popup_bundle_from_popup,
        apply_qty_deal_from_popup,
        bundle_stock_confirm_message,
        build_gratis_choice_message,
        build_gratis_offer_response,
        build_gratis_popup_message,
        build_popup_bundle_message,
        build_qty_deal_message,
        build_qty_deal_offer_response,
        get_active_qty_deal_for_product,
        get_active_gratis_akcija_for_product,
        max_complete_bundle_sets,
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
            quantity = max(1, int(quantity or 1))
            available_sets = max_complete_bundle_sets(cart, popup_bundle_akcija)
            confirm = (request.POST.get('bundle_confirm') or '') == '1'
            if available_sets <= 0:
                msg = bundle_stock_confirm_message(0)
                if stay_on_page:
                    return JsonResponse({'ok': False, 'message': msg}, status=400)
                messages.error(request, msg)
                return redirect('product_detail', slug=slug)
            if quantity > available_sets and not confirm:
                msg = bundle_stock_confirm_message(available_sets)
                if stay_on_page:
                    return JsonResponse({
                        'ok': True,
                        'requires_bundle_confirm': True,
                        'available_sets': available_sets,
                        'requested_sets': quantity,
                        'message': msg,
                    })
                messages.error(request, msg)
                return redirect('product_detail', slug=slug)
            quantity = min(quantity, available_sets)
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
            if request.POST.get('redirect_to') == 'cart':
                return redirect('cart')
            return redirect('product_detail', slug=slug)

    qty_deal_choice = (request.POST.get('qty_deal_choice') or '').strip().lower()

    if stay_on_page and not gratis_choice and not akcija_id and not request.POST.get('flash_offer_id'):
        # 1) Kupi više: iskači tek pri dodavanju u korpu (bez 1 kom u modalu)
        if qty_deal_choice not in ('no', 'skip'):
            qty_offer_akcija = get_active_qty_deal_for_product(product)
            if qty_offer_akcija and cart.remaining_stock(product, variation) >= 2:
                qty_offer = build_qty_deal_offer_response(qty_offer_akcija)
                if qty_offer:
                    return JsonResponse({
                        'ok': True,
                        'requires_qty_deal_choice': True,
                        'qty_deal_offer': qty_offer,
                        'pending_quantity': quantity,
                        'cart_count': len(cart),
                        'message': 'Imaš količinsku ponudu za ovaj artikal.',
                    })
        # 2) + Ponuda (nakon odbijanja kupi više ili ako nema qty deala)
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
        pct = getattr(request, '_flash_discount_percent', None) or promo_akcija.popust_postotak
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

    product = Product.objects.filter(
        aktivan=True, sakriven_do_stanja=False, na_stanju=True, pk=product_id,
    ).first()
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
    stock_changed, stock_notices = cart.clamp_to_stock()
    if stock_changed:
        for notice in dict.fromkeys(stock_notices):
            messages.error(request, notice)
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
        **page_seo_context('cart', defaults={
            'seo_title': 'Korpa — opremazaribolov.ba',
            'seo_description': 'Vaša korpa — opremazaribolov.ba',
            'seo_h1': 'Korpa',
        }),
    }
    return render(request, 'cart.html', context)


@require_POST
def update_cart(request):
    cart = Cart(request)
    capped = False
    for key in list(cart.cart.keys()):
        qty = request.POST.get(f'quantity_{key}')
        if qty is not None:
            try:
                requested = int(qty)
            except (TypeError, ValueError):
                continue
            applied = cart.set_quantity(key, requested)
            if requested > 0 and applied < requested:
                capped = True
    if capped:
        messages.error(request, 'Količina je smanjena na dostupno stanje.')
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
            pct = coupon.postotak
            pct_label = int(pct) if pct == int(pct) else pct
            if coupon.automatski or coupon.loyalty_kartica_id:
                messages.success(
                    request,
                    f'Loyalty kartica primijenjena — popust {pct_label}% '
                    f'(ne vrijedi na artikle na akciji).',
                )
            else:
                messages.success(
                    request,
                    f'Kupon primijenjen — popust {pct_label}%.',
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
    from .loyalty import ba_mobile_local
    profil = getattr(request.user, 'profil', None)
    return {
        'ime_prezime': request.user.get_full_name() or request.user.email,
        'email': request.user.email,
        'telefon': ba_mobile_local(profil.telefon) if profil else '',
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

    stock_changed, stock_notices = cart.clamp_to_stock()
    if stock_changed:
        for notice in dict.fromkeys(stock_notices):
            messages.error(request, notice)
        if not cart.item_count:
            messages.warning(request, 'Korpa je prazna.')
            return redirect('home')
        if request.method == 'POST':
            return redirect('cart')

    form = CheckoutForm(initial=_checkout_initial(request))
    if request.method != 'POST':
        maybe_apply_loyalty_coupon_from_phone(
            cart,
            (form.initial or {}).get('telefon') or '',
        )
    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            maybe_apply_loyalty_coupon_from_phone(
                cart, form.cleaned_data.get('telefon') or '',
            )
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
            try:
                from .views_magacin import invalidate_magacin_nav_counts

                invalidate_magacin_nav_counts()
            except Exception:
                pass
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
                        shipping=order.dostava_naziv,
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

            # Sync loyalty nakon emaila — ne smije blokirati slanje narudžbe na mail.
            # Evidentiraj potrošnju i bez unesenog loyalty koda / popusta (email ili telefon).
            logger.info("Checkout završen, pripremam sync za narudžbu #%s", order.broj)
            try:
                card = azuriraj_loyalty_nakon_narudzbe(order)
                if card:
                    logger.info(
                        "Loyalty potrošnja ažurirana za karticu %s (narudžba #%s, kod nije obavezan)",
                        card.kod,
                        order.broj,
                    )
                    sync_korisnik(card.user)
                elif request.user.is_authenticated:
                    card = getattr(request.user, 'loyalty_kartica', None)
                    if card:
                        sync_korisnik(request.user)
            except Exception:
                logger.exception(
                    'Loyalty ažuriranje nije uspjelo za narudžbu #%s',
                    order.broj,
                )
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
        **page_seo_context('checkout', defaults={
            'seo_title': 'Narudžba — opremazaribolov.ba',
            'seo_description': 'Završite narudžbu — opremazaribolov.ba',
            'seo_h1': 'Narudžba',
        }),
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
        **page_seo_context('order_success', defaults={
            'seo_title': 'Narudžba primljena — opremazaribolov.ba',
            'seo_description': '',
            'seo_h1': 'Hvala na narudžbi!',
        }),
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

                from .live_visitor_offer import (
                    claim_registration_invite_reward,
                    mark_loyalty_popup_registration_pending,
                )
                if (request.POST.get('loyalty_popup') or '').strip() == '1':
                    mark_loyalty_popup_registration_pending(request)
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
        **page_seo_context('register', defaults={
            'seo_title': 'Registracija — opremazaribolov.ba',
            'seo_description': 'Kreirajte nalog — opremazaribolov.ba',
            'seo_h1': 'Registracija',
        }),
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
                request.session.modified = True
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
        **page_seo_context('login', defaults={
            'seo_title': 'Prijava — opremazaribolov.ba',
            'seo_description': 'Prijavite se — opremazaribolov.ba',
            'seo_h1': 'Prijava',
        }),
    }
    return render(request, 'auth/login.html', context)


def logout_view(request):
    logout(request)
    messages.info(request, 'Odjavljeni ste.')
    return redirect('home')


_ACCOUNT_SECTIONS = frozenset({
    'pregled',
    'narudzbe',
    'loyalty',
    'adrese',
    'sacuvani',
    'pregledano',
    'kuponi',
    'reklamacije',
    'postavke',
})

_LOYALTY_TIER_EN = {
    'bronza': 'BRONZE',
    'srebrna': 'SILVER',
    'zlatna': 'GOLD',
    'platinum': 'PLATINUM',
}


def _account_section_from_post(request):
    section = (request.POST.get('account_section') or 'postavke').strip()
    if section not in _ACCOUNT_SECTIONS:
        return 'postavke'
    return section


def _account_recently_viewed(request, limit=10):
    visitor = (
        LiveVisitor.objects.filter(user=request.user)
        .order_by('-last_seen')
        .first()
    )
    if visitor is None:
        session_key = request.session.session_key
        if session_key:
            visitor = LiveVisitor.objects.filter(session_key=session_key).first()
    raw = (visitor.pregledani_proizvodi if visitor else None) or []
    ids = []
    seen = set()
    for item in raw:
        pk = None
        if isinstance(item, dict):
            pk = item.get('id')
        elif isinstance(item, int):
            pk = item
        try:
            pk = int(pk)
        except (TypeError, ValueError):
            continue
        if pk in seen:
            continue
        seen.add(pk)
        ids.append(pk)
        if len(ids) >= limit:
            break
    if not ids:
        return []
    by_id = {
        product.pk: product
        for product in _product_queryset(request).filter(pk__in=ids)
    }
    return [by_id[pk] for pk in ids if pk in by_id]


def _account_dashboard_extras(request, *, orders, loyalty, loyalty_card, profil):
    spend = loyalty_card.ukupna_potrosnja or Decimal('0')
    tier = loyalty.get('tier') or {}
    next_tier = loyalty.get('next_tier')
    cap = tier.get('do') if next_tier else spend
    if cap in (None, Decimal('0')) and next_tier:
        cap = next_tier.get('od')
    if cap:
        progress_pct = int(min(100, max(0, (spend / cap) * 100)))
    else:
        progress_pct = 100
    coupons = list(
        Coupon.objects.filter(
            Q(vlasnik=request.user) | Q(loyalty_kartica=loyalty_card),
            aktivan=True,
        ).order_by('automatski', '-kreiran')
    )
    closed = {Order.Status.ZAVRSENA, Order.Status.OTKAZANA}
    return {
        'orders_recent': orders[:5],
        'orders_count': len(orders),
        'active_orders_count': sum(1 for order in orders if order.status not in closed),
        'coupons': coupons,
        'coupons_count': len(coupons),
        'loyalty_spend': spend,
        'loyalty_cap': cap,
        'loyalty_progress_pct': progress_pct,
        'loyalty_tier_en': _LOYALTY_TIER_EN.get(tier.get('nivo'), (tier.get('label') or '').upper()),
        'recommended_products': _home_featured_products(request),
        'recently_viewed_products': _account_recently_viewed(request),
        'home_trust_items': _home_trust_items(),
        'has_address': bool((profil.adresa or '').strip() or (profil.grad or '').strip()),
    }


@login_required(login_url='login')
def account(request):
    from .loyalty import ba_mobile_local

    profil, _ = UserProfile.objects.get_or_create(user=request.user)
    profile_form = ProfileForm(initial={
        'ime_prezime': request.user.get_full_name() or request.user.first_name,
        'email': request.user.email,
        'telefon': ba_mobile_local(profil.telefon) if profil.telefon else '',
        'adresa': profil.adresa,
        'grad': profil.grad,
        'postanski_broj': profil.postanski_broj,
    }, exclude_user_id=request.user.pk)

    account_initial_section = 'pregled'
    if request.method == 'POST':
        account_initial_section = _account_section_from_post(request)
        profile_form = ProfileForm(request.POST, exclude_user_id=request.user.pk)
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
                return redirect(f"{reverse('account')}#{account_initial_section}")

    orders = list(
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
    welcome_name = (
        (request.user.get_full_name() or request.user.first_name or '').strip()
        or cardholder_name
    )

    context = {
        **_base_context(),
        'profile_form': profile_form,
        'orders': orders,
        'loyalty': loyalty,
        'cardholder_name': cardholder_name,
        'welcome_name': welcome_name,
        'account_initial_section': account_initial_section,
        'profil': profil,
        **_account_dashboard_extras(
            request,
            orders=orders,
            loyalty=loyalty,
            loyalty_card=loyalty_card,
            profil=profil,
        ),
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


def _clone_uploaded_image(uploaded_file):
    """Kopija uploadane slike — isti file se može sačuvati na više artikala."""
    if hasattr(uploaded_file, 'seek'):
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
    data = uploaded_file.read()
    if hasattr(uploaded_file, 'seek'):
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
    name = (getattr(uploaded_file, 'name', None) or 'slika.jpg').replace('\\', '/').rsplit('/', 1)[-1]
    return ContentFile(data, name=name or 'slika.jpg')


def _parse_staff_product_ids(raw_ids, *, limit=200):
    ids = []
    seen = set()
    for raw in raw_ids or []:
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            continue
        if pk <= 0 or pk in seen:
            continue
        seen.add(pk)
        ids.append(pk)
        if len(ids) >= limit:
            break
    return ids


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


def _staff_parse_tag_names(raw_text):
    """
    Ručni unos tagova: zarez / novi red / ; / | dijeli tagove.
    Razmaci unutar taga ostaju (npr. „fider strune”).
    """
    if not raw_text:
        return []
    text = str(raw_text).replace('\u00a0', ' ').replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace(';', '\n').replace('|', '\n')
    names = []
    seen = set()
    for line in text.split('\n'):
        for part in line.split(','):
            name = re.sub(r'\s+', ' ', (part or '').strip())
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name[:50])
    return names


def _staff_resolve_product_tags(request):
    """
    Spoji chipove (tag_ids) + ručni unos (tag_names / tagovi_tekst).
    Novi nazivi se kreiraju (get_or_create).
    """
    from .models import Tag

    by_id = {}
    for tid in _staff_parse_tag_ids(request):
        tag = Tag.objects.filter(pk=tid).first()
        if tag:
            by_id[tag.pk] = tag

    free_chunks = []
    free_chunks.extend(request.POST.getlist('tag_names'))
    free_chunks.append(request.POST.get('tagovi_tekst') or '')
    created_any = False
    for chunk in free_chunks:
        for name in _staff_parse_tag_names(chunk):
            try:
                tag, created = Tag.get_or_create_by_name(name)
            except ValueError:
                continue
            by_id[tag.pk] = tag
            if created:
                created_any = True

    if created_any:
        try:
            invalidate_product_tag_search_cache()
        except Exception:
            pass

    return list(by_id.values())


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
    from .magacin import MagacinError, validate_order_stock

    order = get_object_or_404(Order, broj=broj)
    if order.status == Order.Status.OTKAZANA:
        messages.info(request, f'Narudžba #{broj} je otkazana.')
        return False
    if order.status != Order.Status.ZAVRSENA:
        order.status = Order.Status.ZAVRSENA
        order.save(update_fields=['status'])
    try:
        if order.lager_status != Order.LagerStatus.VALIDIRANO:
            validate_order_stock(order, user=request.user)
    except MagacinError as exc:
        messages.error(request, str(exc))
        return False
    messages.success(request, f'Narudžba #{broj} je validirana.')
    return True


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_lookup(request):
    query = request.GET.get('q', '').strip()
    url = reverse('staff_online_orders')
    if query:
        url = f'{url}?{urlencode({"q": query})}'
    return redirect(url)


def _create_odoo_sale_order_from_web(request, broj, *, force=False):
    """Staff: kreiraj Odoo Sales narudžbu iz web narudžbe."""
    from .odoo_client import OdooError
    from .odoo_sales import create_odoo_sale_order_for_web_order

    order = Order.objects.filter(broj=broj).prefetch_related('stavke').first()
    if not order:
        messages.error(request, f'Narudžba #{broj} nije pronađena.')
        return None
    force = force or (request.POST.get('force') in ('1', 'true', 'yes', 'on'))
    try:
        result = create_odoo_sale_order_for_web_order(order, force=force)
    except OdooError as exc:
        messages.error(request, f'Odoo greška: {exc}')
        return None
    except Exception as exc:
        logger.exception('Odoo SO create failed for #%s', broj)
        messages.error(request, f'Odoo greška: {exc}')
        return None

    if result.get('ok'):
        if result.get('existing') and not force:
            messages.info(request, result.get('message') or 'Već postoji u Odoo.')
        else:
            messages.success(request, result.get('message') or 'Odoo narudžba kreirana.')
    else:
        messages.error(request, result.get('message') or 'Odoo narudžba nije kreirana.')
    return result


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_detail(request, broj):
    order = get_object_or_404(
        Order.objects.prefetch_related('stavke'),
        broj=broj,
    )

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'zavrsi':
            _mark_order_completed(request, broj)
            return redirect('staff_magacin_narudzbe')
        if action == 'odoo_narudzba':
            _create_odoo_sale_order_from_web(request, broj)
            return redirect('staff_order_detail', broj=broj)
        if action == 'xexpress':
            _send_order_to_xexpress(request, broj)
            return redirect('staff_order_detail', broj=broj)
        if action == 'validiraj':
            from .magacin import MagacinError, validate_order_stock
            try:
                validate_order_stock(order, user=request.user)
                messages.success(request, f'Narudžba #{order.broj} je validirana — zaliha je skinuta s lokacija.')
            except MagacinError as exc:
                messages.error(request, str(exc))
                return redirect('staff_order_detail', broj=broj)
            return redirect('staff_magacin_narudzbe')
        if action == 'otkazi':
            from .magacin import MagacinError, cancel_order_stock
            try:
                cancel_order_stock(order, user=request.user)
                messages.success(request, f'Narudžba #{order.broj} je otkazana — rezervacija je vraćena.')
            except MagacinError as exc:
                messages.error(request, str(exc))
            return redirect('staff_magacin_narudzbe')

    from .magacin import order_is_editable
    from .views_magacin import _magacin_context
    can_edit = (
        getattr(order, 'izvor', '') == Order.Izvor.MAGACIN
        and order_is_editable(order)
    )
    context = {
        **_magacin_context(request, section='narudzbe', page_title=f'Narudžba #{order.broj}'),
        **get_order_email_context(order),
        'can_edit_order': can_edit,
        'edit_order_url': (
            f"{reverse('staff_magacin_narudzba_nova')}?broj={order.broj}" if can_edit else ''
        ),
    }
    return render(request, 'staff/order_detail.html', context)


def _send_order_to_xexpress(request, broj):
    from .xexpress_service import XExpressAlreadySent, XExpressError, create_shipment

    order = Order.objects.filter(broj=broj).first()
    if order is None:
        messages.error(request, f'Narudžba #{broj} nije pronađena.')
        return None
    try:
        result = create_shipment(order)
    except XExpressAlreadySent as exc:
        messages.info(request, str(exc))
        return None
    except XExpressError as exc:
        messages.error(request, str(exc))
        return None
    except Exception as exc:
        logger.exception('X-Express slanje nije uspjelo za #%s', broj)
        messages.error(request, f'X-Express greška: {exc}')
        return None
    sifra = (result or {}).get('sifra') or order.xexpress_sifra
    if (result or {}).get('duplicate'):
        messages.success(
            request,
            f'Pošiljka već postoji u X-Express. Šifra: {sifra}.',
        )
    else:
        messages.success(
            request,
            f'Pošiljka je unijeta na X-Express nalog (Priprema). Šifra: {sifra}. '
            'Na online.x-express.ba otvori meni Pošiljke.',
        )
    return result


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_order_xexpress(request, broj):
    _send_order_to_xexpress(request, broj)
    nxt = (request.POST.get('next') or '').strip()
    if nxt.startswith('/') and not nxt.startswith('//'):
        return redirect(nxt)
    return redirect('staff_order_detail', broj=broj)


def _order_print_job(order):
    packing_lines, odoo_error = _build_order_packing_lines(order)
    packing_missing = [
        line for line in packing_lines
        if line.get('check_mp') or not line.get('picks')
    ]
    job = get_order_email_context(order)
    job.update({
        'packing_lines': packing_lines,
        'odoo_error': odoo_error,
        'packing_missing': packing_missing,
        'requires_mp_check': bool(packing_missing),
    })
    return job


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_print(request, broj):
    """Štampa: samo faktura (račun + garancija). Pakovanje je u Magacinu."""
    order = get_object_or_404(
        Order.objects.prefetch_related('stavke', 'magacin_holds'),
        broj=broj,
    )
    from .views_magacin import _provjera_url, order_needs_mp_check

    if order_needs_mp_check(order):
        messages.warning(
            request,
            f'Narudžba #{order.broj} ima artikal iz maloprodaje. '
            'Prvo označi Ima u MP ili Nema, pa štampaj.',
        )
        return redirect(_provjera_url(order.broj, next_print=True))
    job = _order_print_job(order)
    context = {
        **job,
        'print_jobs': [job],
        'print_brojevi': [order.broj],
        'requires_mp_check': job['requires_mp_check'],
        'mark_printed_url': reverse('staff_order_mark_printed', kwargs={'broj': order.broj}),
    }
    return render(request, 'staff/order_print.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_order_mark_printed(request, broj):
    """Označi narudžbu kao odštampanu (zeleni check na listi)."""
    from django.utils import timezone

    order = get_object_or_404(Order, broj=broj)
    if not order.odstampana:
        order.odstampana = True
        order.odstampana_at = timezone.now()
        order.save(update_fields=['odstampana', 'odstampana_at'])
    return JsonResponse({
        'ok': True,
        'broj': order.broj,
        'odstampana': True,
        'odstampana_at': order.odstampana_at.isoformat() if order.odstampana_at else None,
    })


def _deduct_order_stock_from_packing(order):
    """
    Skini zalihu iz Odoa po packing lokacijama (abecedno, kao na listi pakovanja).
    Vraća (ok, message, details).
    """
    from .odoo_client import OdooClient, OdooError, odoo_je_konfigurisan

    if order.stanje_skinuto:
        return False, 'Stanje je već skinuto za ovu narudžbu.', []

    packing_lines, odoo_error = _build_order_packing_lines(order)
    if odoo_error and not packing_lines:
        return False, f'Odoo: {odoo_error}', []

    picks = []
    skipped = []
    for line in packing_lines:
        product_id = line.get('odoo_product_id')
        line_picks = line.get('picks') or []
        if not product_id or not line_picks:
            skipped.append({
                'naziv': line.get('naziv'),
                'sifra': line.get('sifra'),
                'reason': line.get('pick_text') or 'Provjeri u MP',
            })
            continue
        for pick in line_picks:
            loc_id = pick.get('location_id')
            if not loc_id:
                skipped.append({
                    'naziv': line.get('naziv'),
                    'sifra': line.get('sifra'),
                    'reason': f"Nema location_id za {pick.get('location_name')}",
                })
                continue
            picks.append({
                'product_id': product_id,
                'location_id': loc_id,
                'location_name': pick.get('location_name'),
                'take': pick.get('take'),
                'naziv': line.get('naziv'),
                'sifra': line.get('sifra'),
            })

    if not picks:
        return False, 'Nema Odoo lokacija za skidanje (sve je „Provjeri u MP” ili nema zalihe).', skipped

    if not odoo_je_konfigurisan():
        return False, 'Odoo nije konfigurisan.', []

    try:
        client = OdooClient.from_settings()
        results = client.deduct_stock_picks(
            picks,
            origin=f'Online narudžba #{order.broj}',
        )
    except OdooError as exc:
        return False, str(exc), []
    except Exception as exc:
        return False, f'Odoo greška: {exc}', []

    ok_results = [r for r in results if r.get('ok')]
    fail_results = [r for r in results if not r.get('ok')]

    # Ažuriraj lokalno stanje artikala (samo uspješno skinute količine)
    from .models import Product, ProductVariation

    qty_by_product = {}
    for pick, res in zip(picks, results):
        if not res.get('ok'):
            continue
        pid = pick.get('product_id')
        qty_by_product[pid] = qty_by_product.get(pid, 0) + int(res.get('quantity') or 0)

    for odoo_pid, qty in qty_by_product.items():
        variation = ProductVariation.objects.filter(odoo_variant_id=odoo_pid).select_related('artikal').first()
        if variation:
            variation.stanje = max(0, int(variation.stanje or 0) - qty)
            if variation.stanje == 0:
                variation.na_stanju = False
            variation.save(update_fields=['stanje', 'na_stanju'])
            continue
        product = Product.objects.filter(odoo_template_id=odoo_pid).first()
        if product:
            product.stanje = max(0, int(product.stanje or 0) - qty)
            if product.stanje == 0:
                product.na_stanju = False
            product.save(update_fields=['stanje', 'na_stanju'])

    details = {
        'ok': ok_results,
        'fail': fail_results,
        'skipped': skipped,
    }

    if not ok_results:
        msg = 'Ništa nije skinuto iz Odoa.'
        if fail_results:
            msg += ' ' + '; '.join(
                f"{f.get('location_name')}: {f.get('error')}" for f in fail_results[:5]
            )
        return False, msg, details

    # Označi gotovo čim je nešto skinuto — da se ne dupla pri ponovnom kliku
    from django.utils import timezone
    order.stanje_skinuto = True
    order.stanje_skinuto_at = timezone.now()
    order.save(update_fields=['stanje_skinuto', 'stanje_skinuto_at'])

    msg = f'Skinuto {len(ok_results)} stavk(e/i) sa Odoo lokacija.'
    if fail_results:
        msg += (
            f' Greške ({len(fail_results)}): '
            + '; '.join(f"{f.get('location_name')}: {f.get('error')}" for f in fail_results[:3])
            + ' — provjeri ručno u Odoo (ne skidaj ponovo ovdje).'
        )
    if skipped:
        msg += f' Preskočeno (MP/bez zalihe): {len(skipped)}.'
    return True, msg, details


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_brza_posta(request, broj):
    """Podaci za unos u Brzu poštu + dugme Skini sa stanja (Odoo lokacije)."""
    from django.utils import timezone
    from django.contrib import messages as dj_messages

    order = get_object_or_404(Order, broj=broj)
    deduct_details = None
    deduct_ok = None

    if request.method == 'POST' and request.POST.get('action') == 'skini_stanje':
        if order.stanje_skinuto:
            dj_messages.warning(request, 'Stanje je već skinuto za ovu narudžbu.')
        else:
            ok, msg, details = _deduct_order_stock_from_packing(order)
            deduct_ok = ok
            deduct_details = details
            order.refresh_from_db()
            if ok:
                dj_messages.success(request, msg)
            else:
                dj_messages.error(request, msg)

    packing_lines, packing_error = _build_order_packing_lines(order)
    created = timezone.localtime(order.kreirana)
    context = {
        'order': order,
        'datum': created.strftime('%d.%m.%Y.'),
        'vrijeme': created.strftime('%H:%M'),
        'site_name': 'opremazaribolov.ba',
        'iznos_sa_dostavom': order.ukupno,
        'iznos_copy': f'{order.ukupno:.2f}'.replace('.', ','),
        'packing_lines': packing_lines,
        'packing_error': packing_error,
        'deduct_details': deduct_details,
        'deduct_ok': deduct_ok,
    }
    return render(request, 'staff/order_brza_posta.html', context)




def _order_item_odoo_product_id(item, template_variants):
    """
    Pronađi product.product id za stavku narudžbe.
    1) varijacija.odoo_variant_id
    2) artikal.odoo_template_id → varijante (match šifre ili prva ako je jedna)
    """
    variation = getattr(item, 'varijacija', None)
    if variation and variation.odoo_variant_id:
        return int(variation.odoo_variant_id)

    product = getattr(item, 'artikal', None)
    template_id = None
    if variation and variation.odoo_template_id:
        template_id = int(variation.odoo_template_id)
    elif product and product.odoo_template_id:
        template_id = int(product.odoo_template_id)

    if not template_id:
        return None

    variants = template_variants.get(template_id) or []
    if not variants:
        return None
    if len(variants) == 1:
        return int(variants[0]['id'])

    sifra = (getattr(item, 'sifra', None) or '').strip().casefold()
    if sifra:
        for variant in variants:
            code = (variant.get('default_code') or '')
            if str(code).strip().casefold() == sifra:
                return int(variant['id'])
    return None


def _allocate_packing_locations(needed_qty, stock_locations):
    """
    Raspodijeli količinu po lokacijama sortiranima abecedno.
    stock_locations: [{location_name, quantity}, ...] već sortirano.
    """
    remaining = max(0, int(needed_qty or 0))
    picks = []
    for loc in stock_locations or []:
        if remaining <= 0:
            break
        on_hand = max(0, int(loc.get('quantity') or 0))
        if on_hand <= 0:
            continue
        take = min(on_hand, remaining)
        picks.append({
            'location_name': loc.get('location_name') or '?',
            'location_id': loc.get('location_id'),
            'take': take,
            'on_hand': on_hand,
        })
        remaining -= take
    return picks, remaining


def _magacin_stock_picks(items):
    """Picks iz lokalnog Magacin stanja kad nema rezervacije (npr. webshop)."""
    from .magacin import order_location_rows

    picks_by_item = {}
    for item in items:
        if not item.artikal_id:
            continue
        rows, _ = order_location_rows(item.artikal, item.varijacija)
        remaining = int(item.kolicina or 0)
        picks = []
        for row in rows:
            if remaining <= 0:
                break
            dostupno = max(0, int(row.get('dostupno') or 0))
            if dostupno <= 0:
                continue
            take = min(dostupno, remaining)
            loc = row['location']
            picks.append({
                'location_name': loc.sifra or loc.naziv or '?',
                'location_id': loc.pk,
                'take': take,
                'on_hand': int(row.get('kolicina') or dostupno),
                'location_path': loc.odoo_location_path or loc.naziv or '',
            })
            remaining -= take
        if picks:
            picks_by_item[item.pk] = (picks, remaining)
    return picks_by_item


def _magacin_hold_picks(order, items):
    """Picks iz lokalnih Magacin rezervacija (rezervisano / validirano)."""
    from .models import OrderStockHold

    holds = list(
        order.magacin_holds.exclude(status=OrderStockHold.Status.OTKAZANO)
        .select_related('location', 'product', 'variation')
    )
    if not holds:
        return {}

    buckets = [
        {
            'product_id': hold.product_id,
            'variation_id': hold.variation_id,
            'left': int(hold.kolicina or 0),
            'location': hold.location,
        }
        for hold in holds
    ]

    def consume(item, variation_id, need):
        picks = []
        for bucket in buckets:
            if need <= 0:
                break
            if bucket['product_id'] != item.artikal_id:
                continue
            if bucket['variation_id'] != variation_id:
                continue
            take = min(bucket['left'], need)
            if take <= 0:
                continue
            loc = bucket['location']
            picks.append({
                'location_name': loc.sifra or loc.naziv or '?',
                'location_id': loc.pk,
                'take': take,
                'on_hand': take,
                'location_path': loc.odoo_location_path or loc.naziv or '',
            })
            bucket['left'] -= take
            need -= take
        return picks, need

    picks_by_item = {}
    for item in items:
        if not item.artikal_id:
            continue
        need = int(item.kolicina or 0)
        picks, need = consume(item, item.varijacija_id, need)
        if need > 0 and item.varijacija_id:
            extra, need = consume(item, None, need)
            picks.extend(extra)
        if not picks:
            continue
        picks = sorted(picks, key=lambda p: (p.get('location_name') or '').casefold())
        picks_by_item[item.pk] = (picks, max(0, need))
    return picks_by_item


def _mp_confirmed_item_ids(order):
    """Stavke potvrđene sa „Ima u MP” — ne idu na police magacina."""
    confirmed = set()
    state = order.pick_state if isinstance(getattr(order, 'pick_state', None), dict) else {}
    for key, row in state.items():
        if not isinstance(row, dict) or not row.get('mp_checked'):
            continue
        if row.get('done') and not row.get('got'):
            continue
        item_id = row.get('item_id')
        if not item_id and isinstance(key, str) and key.endswith(':Provjeri u MP'):
            raw = key.split(':', 1)[0]
            try:
                item_id = int(raw)
            except (TypeError, ValueError):
                item_id = None
        if item_id:
            confirmed.add(int(item_id))
    return confirmed


def _mp_found_qty(pick_state, item_id, default):
    saved = (pick_state or {}).get(f'{item_id}:Provjeri u MP') or {}
    if not isinstance(saved, dict) or not saved.get('mp_checked') or 'mp_found' not in saved:
        return default
    try:
        return max(0, int(saved.get('mp_found') or 0))
    except (TypeError, ValueError):
        return default


def _build_order_packing_lines(order):
    """
    Stavke pakovanja: prvo Magacin rezervacije, inače Odoo lokacije.
    Lokacije se čiste abecedno; količina se uzima redom s prvih lokacija.
    """
    from .odoo_client import OdooClient, OdooError, odoo_je_konfigurisan
    from .magacin import NIJE_POPISAN_LABEL, order_has_nije_popisan

    items = list(
        order.stavke.select_related('artikal', 'artikal__brend', 'artikal__kategorija', 'varijacija').all()
    )
    lines = []
    odoo_error = None
    stock_by_product = {}
    template_variants = {}
    magacin_picks = _magacin_hold_picks(order, items)
    if len(magacin_picks) < len(items):
        for pk, val in _magacin_stock_picks(items).items():
            magacin_picks.setdefault(pk, val)
    mp_confirmed = _mp_confirmed_item_ids(order)
    pick_state = order.pick_state if isinstance(getattr(order, 'pick_state', None), dict) else {}

    if odoo_je_konfigurisan() and items and not magacin_picks:
        try:
            client = OdooClient.from_settings()
            template_ids = set()
            direct_product_ids = set()

            for item in items:
                variation = item.varijacija
                if variation and variation.odoo_variant_id:
                    direct_product_ids.add(int(variation.odoo_variant_id))
                if variation and variation.odoo_template_id:
                    template_ids.add(int(variation.odoo_template_id))
                elif item.artikal and item.artikal.odoo_template_id:
                    template_ids.add(int(item.artikal.odoo_template_id))

            if template_ids:
                template_variants = client.get_product_ids_for_templates(list(template_ids))
                for variants in template_variants.values():
                    for variant in variants:
                        direct_product_ids.add(int(variant['id']))

            if direct_product_ids:
                # for_packing: bez „Prenos u MP” i sličnih transfer lokacija
                stock_by_product = client.get_internal_stock_quants(
                    list(direct_product_ids),
                    for_packing=True,
                )
        except OdooError as exc:
            odoo_error = str(exc)
        except Exception as exc:
            odoo_error = f'Odoo greška: {exc}'

    for index, item in enumerate(items, start=1):
        if getattr(item, 'rezervni_dio', False) and not item.artikal_id:
            qty = int(item.kolicina or 0)
            part_name = (item.naziv or '').strip()
            display_name = part_name or item.product_naziv or 'Rezervni dio'
            lines.append({
                'rb': index,
                'item_id': item.pk,
                'naziv': display_name,
                'sifra': item.sifra or 'REZERVNI',
                'barkod': '',
                'slika': '',
                'brend': '',
                'kategorija': '',
                'kolicina': qty,
                'odoo_product_id': None,
                'picks': [{
                    'location_name': 'Rezervni dio',
                    'location_id': None,
                    'location_path': 'Slanje rezervnog dijela',
                    'take': qty,
                    'on_hand': qty,
                }],
                'pick_text': f'{qty}× {part_name or "Rezervni dio"}',
                'shortfall': 0,
                'check_mp': False,
                'stock_locations': [],
                'rezervni': True,
            })
            continue
        odoo_product_id = _order_item_odoo_product_id(item, template_variants)
        stock_locations = stock_by_product.get(odoo_product_id, []) if odoo_product_id else []
        if item.pk in magacin_picks:
            picks, shortfall = magacin_picks[item.pk]
        elif item.pk in mp_confirmed:
            picks, shortfall = [], int(item.kolicina or 0)
        else:
            picks, shortfall = _allocate_packing_locations(item.kolicina, stock_locations)
        if item.pk in mp_confirmed and shortfall > 0:
            picks = list(picks or [])
            mp_take = _mp_found_qty(pick_state, item.pk, shortfall)
            if mp_take > 0:
                picks.append({
                    'location_name': 'MP',
                    'location_id': None,
                    'take': mp_take,
                    'on_hand': mp_take,
                })
            shortfall = 0
        if shortfall > 0 and order_has_nije_popisan(order, item):
            picks = list(picks or [])
            picks.append({
                'location_name': NIJE_POPISAN_LABEL,
                'location_id': None,
                'take': shortfall,
                'on_hand': shortfall,
                'location_path': NIJE_POPISAN_LABEL,
            })
            shortfall = 0
        if picks:
            picks = sorted(
                picks,
                key=lambda p: (
                    1 if (p.get('location_name') or '') in {'MP', 'Provjeri u MP', 'Nije popisan'} else 0,
                    (p.get('location_name') or '').casefold(),
                ),
            )
        # Ako ima zalihe: lokacija + koliko uzimaš; inače ostatak → Provjeri u MP (online) ili Nije popisan
        if picks:
            pick_parts = [
                f"{p['take']}× {p['location_name']}"
                for p in picks
            ]
            if shortfall > 0:
                pick_parts.append('Provjeri u MP')
            pick_text = ' · '.join(pick_parts)
        else:
            pick_text = 'Provjeri u MP'

        display_name = item.product_naziv or item.naziv
        if item.varijacija_naziv:
            display_name = f'{display_name} — {item.varijacija_naziv}'
        if getattr(item, 'rezervni_dio', False):
            parent_name = ''
            if item.artikal_id:
                parent_name = item.artikal.naziv or ''
            parent_name = parent_name or item.product_naziv or ''
            part_name = (item.naziv or '').strip()
            if parent_name and part_name and part_name.casefold() != parent_name.casefold():
                display_name = f'{parent_name} — {part_name}'
            else:
                display_name = parent_name or part_name or display_name

        product = item.artikal
        brend = ''
        kategorija = ''
        if product is not None:
            if getattr(product, 'brend', None):
                brend = product.brend.naziv or ''
            if getattr(product, 'kategorija', None):
                kategorija = product.kategorija.naziv or ''
        variation = item.varijacija
        barkod = ''
        slika = ''
        if variation:
            barkod = (getattr(variation, 'barkod', None) or '').strip()
            img = getattr(variation, 'prikazna_slika', None)
            if img:
                try:
                    slika = img.url
                except Exception:
                    slika = ''
        if product:
            if not barkod:
                barkod = (product.barkod or '').strip()
            if not slika and product.prikazna_slika:
                try:
                    slika = product.prikazna_slika.url
                except Exception:
                    slika = ''

        lines.append({
            'rb': index,
            'item_id': item.pk,
            'naziv': display_name,
            'sifra': item.sifra or '',
            'barkod': barkod,
            'slika': slika,
            'brend': brend,
            'kategorija': kategorija,
            'kolicina': item.kolicina,
            'odoo_product_id': odoo_product_id,
            'picks': picks,
            'pick_text': pick_text,
            'shortfall': shortfall,
            'check_mp': (
                not getattr(item, 'rezervni_dio', False)
                and item.pk not in mp_confirmed
                and (not picks or shortfall > 0)
                and not any((p.get('location_name') or '') == 'Nije popisan' for p in (picks or []))
            ),
            'stock_locations': stock_locations,
            'rezervni': bool(getattr(item, 'rezervni_dio', False)),
        })

    return lines, odoo_error


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_order_packing(request, broj):
    """Pakovanje: artikli narudžbe + Odoo lokacije (abecedno, quantity on hand)."""
    from django.utils import timezone

    order = get_object_or_404(Order, broj=broj)
    packing_lines, odoo_error = _build_order_packing_lines(order)
    created = timezone.localtime(order.kreirana)
    context = {
        'order': order,
        'packing_lines': packing_lines,
        'odoo_error': odoo_error,
        'datum': created.strftime('%d.%m.%Y.'),
        'vrijeme': created.strftime('%H:%M'),
        'site_name': 'opremazaribolov.ba',
    }
    return render(request, 'staff/order_packing.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_toggle_edit_mode(request):
    """Uključi/isključi edit mode na sajtu (superuser)."""
    if 'enabled' in request.POST or 'edit_mode' in request.POST:
        raw = (request.POST.get('enabled') or request.POST.get('edit_mode') or '').strip().lower()
        enabled = raw in ('1', 'true', 'on', 'yes', 'da')
    else:
        enabled = not _staff_edit_mode_enabled(request)
    request.session[STAFF_EDIT_MODE_SESSION_KEY] = enabled
    request.session.modified = True
    if enabled:
        messages.success(
            request,
            'Edit uključen — na artiklima piše šta fali, klik otvara Brzi unos.',
        )
    else:
        messages.success(request, 'Edit isključen.')
    next_url = (request.POST.get('next') or request.META.get('HTTP_REFERER') or '').strip()
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('account')


# Polja SiteSettings koja se smiju mijenjati s fronta u edit modu
_SITE_EDIT_TEXT_FIELDS = frozenset({
    'naslov_novo', 'podnaslov_novo',
    'naslov_izdvojeno', 'podnaslov_izdvojeno',
    'naslov_akcija', 'podnaslov_akcija',
    'naslov_brendovi',
    'tekst_pogledaj_sve',
    'naslov_blog',
    'newsletter_naslov', 'newsletter_podnaslov',
    'newsletter_placeholder', 'newsletter_dugme', 'newsletter_napomena',
    'naslov_povezani', 'podnaslov_povezani',
    'promo_bar_tekst', 'promo_bar_link_tekst',
    'dostava_naziv',
    'tekst_dugme_korpa', 'tekst_dugme_rasprodato',
})
_SITE_EDIT_COLOR_FIELDS = frozenset({
    'boja_dugme_korpa', 'boja_dugme_korpa_hover',
    'boja_ikonica_korpa',
    'boja_dugme_banner', 'boja_dugme_banner_hover',
    'kontakt_boja_whatsapp', 'kontakt_boja_viber', 'kontakt_boja_messenger',
})
_SITE_EDIT_MAX_LEN = {
    'naslov_novo': 120,
    'podnaslov_novo': 200,
    'naslov_izdvojeno': 120,
    'podnaslov_izdvojeno': 200,
    'naslov_akcija': 120,
    'podnaslov_akcija': 200,
    'naslov_brendovi': 120,
    'tekst_pogledaj_sve': 40,
    'naslov_blog': 200,
    'newsletter_naslov': 120,
    'newsletter_podnaslov': 240,
    'newsletter_placeholder': 80,
    'newsletter_dugme': 40,
    'newsletter_napomena': 160,
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
        'is_superuser_staff': request.user.is_superuser,
        'new_orders_count': nova_count,
    }
    return render(request, 'staff/admin_panel.html', context)


GIFT_VOUCHER_AMOUNTS = (50, 100, 200, 300, 500)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_gift_voucher(request):
    context = {
        **_base_context(),
        'amounts': GIFT_VOUCHER_AMOUNTS,
        'ime': (request.GET.get('ime') or '').strip(),
        'prezime': (request.GET.get('prezime') or '').strip(),
        'iznos': (request.GET.get('iznos') or '').strip(),
    }
    return render(request, 'staff/gift_voucher.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_gift_voucher_print(request):
    from django.utils import timezone

    ime = (request.GET.get('ime') or '').strip()
    prezime = (request.GET.get('prezime') or '').strip()
    try:
        iznos = int(request.GET.get('iznos') or 0)
    except (TypeError, ValueError):
        iznos = 0
    if not ime or not prezime or iznos not in GIFT_VOUCHER_AMOUNTS:
        messages.error(request, 'Unesi ime, prezime i iznos poklona (50, 100, 200, 300 ili 500 KM).')
        return redirect('staff_gift_voucher')
    created = timezone.localtime(timezone.now())
    context = {
        'ime': ime,
        'prezime': prezime,
        'punio_ime': f'{ime} {prezime}'.strip(),
        'iznos': iznos,
        'datum': created.strftime('%d.%m.%Y.'),
        'site_name': 'opremazaribolov.ba',
    }
    return render(request, 'staff/gift_voucher_print.html', context)


def _uvoz_search_articles(query: str) -> list[dict]:
    """
    Pretraga artikala kroz SVE uvoze po nazivu.
    Grupiše po nazivu i prikaže historiju (cijena, količina, marža) s izmjenama.
    """
    from collections import OrderedDict

    from .models import UvozStavka

    q = (query or '').strip()
    if len(q) < 2:
        return []

    stavke = list(
        UvozStavka.objects.filter(artikal_naziv__icontains=q, uvoz__izvor='sajt')
        .select_related('uvoz', 'product')
        .order_by('artikal_naziv', 'uvoz__kreiran', 'id')
    )
    if not stavke:
        return []

    groups: OrderedDict[str, list] = OrderedDict()
    for s in stavke:
        key = (s.artikal_naziv or '').strip()
        groups.setdefault(key, []).append(s)

    def _fmt_money(v):
        if v is None:
            return '—'
        return f'{v} KM'

    def _fmt_qty(v):
        if v is None:
            return '—'
        try:
            if v == v.to_integral_value():
                return str(int(v))
        except Exception:
            pass
        return str(v)

    results = []
    for name, items in groups.items():
        timeline = []
        prev = None
        for s in items:
            changes = []
            if prev is not None:
                if s.mpc_brutto != prev.mpc_brutto:
                    changes.append({
                        'field': 'Mpc brutto',
                        'from': _fmt_money(prev.mpc_brutto),
                        'to': _fmt_money(s.mpc_brutto),
                    })
                if s.kolicina != prev.kolicina:
                    changes.append({
                        'field': 'Količina',
                        'from': _fmt_qty(prev.kolicina),
                        'to': _fmt_qty(s.kolicina),
                    })
                if s.vpc_marza != prev.vpc_marza:
                    changes.append({
                        'field': 'Vpc marža',
                        'from': prev.vpc_marza_pct_display or '—',
                        'to': s.vpc_marza_pct_display or '—',
                    })
                if s.nabavna != prev.nabavna:
                    changes.append({
                        'field': 'Nabavna',
                        'from': _fmt_money(prev.nabavna),
                        'to': _fmt_money(s.nabavna),
                    })
                if s.fakturna != prev.fakturna:
                    changes.append({
                        'field': 'Fakturna',
                        'from': _fmt_money(prev.fakturna),
                        'to': _fmt_money(s.fakturna),
                    })
                if s.vpc_netto != prev.vpc_netto:
                    changes.append({
                        'field': 'Vpc netto',
                        'from': _fmt_money(prev.vpc_netto),
                        'to': _fmt_money(s.vpc_netto),
                    })
            timeline.append({
                'stavka': s,
                'changes': changes,
                'is_first': prev is None,
            })
            prev = s

        # prikaži novije prvo u UI
        timeline_newest_first = list(reversed(timeline))
        product = None
        for s in reversed(items):
            if s.product_id:
                product = s.product
                break
        results.append({
            'naziv': name,
            'count': len(items),
            'product': product,
            'timeline': timeline_newest_first,
            'latest': items[-1],
            'has_changes': any(t['changes'] for t in timeline),
        })

    # više pogodaka / više uvoza gore
    results.sort(key=lambda r: (-r['count'], r['naziv'].casefold()))
    return results


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_uvoz(request):
    """
    Lista sačuvanih uvoza + upload novog Excel-a + pretraga artikala kroz uvoze.
    """
    from .models import Uvoz
    from .uvoz_import import apply_uvoz_import, create_uvoz_from_rows, parse_uvoz_excel

    result = None
    dry_run = False
    search_q = (request.GET.get('q') or '').strip()
    search_results = _uvoz_search_articles(search_q) if search_q else []

    if request.method == 'POST' and request.POST.get('action') == 'import':
        dry_run = (request.POST.get('dry_run') or '').strip() in ('1', 'true', 'on', 'yes')
        uploaded = request.FILES.get('excel_file')
        custom_name = (request.POST.get('naziv') or '').strip()
        if not uploaded:
            messages.error(request, 'Odaberi Excel fajl (.xlsx).')
        else:
            try:
                rows = parse_uvoz_excel(uploaded)
                if not rows:
                    messages.warning(
                        request,
                        'U fajlu nema redova artikala ispod zaglavlja Artikal / Mpc brutto.',
                    )
                elif dry_run:
                    result = apply_uvoz_import(rows, dry_run=True)
                    messages.info(
                        request,
                        f'Pregled (nije snimljeno): {result["updated"]} ažuriranja, '
                        f'{result["created"]} novih, {result["rows_qty_positive"]} sa količinom > 0.',
                    )
                else:
                    uvoz, result = create_uvoz_from_rows(
                        rows,
                        fajl_naziv=getattr(uploaded, 'name', '') or '',
                        naziv=custom_name,
                        user=request.user,
                        apply_to_products=True,
                    )
                    messages.success(
                        request,
                        f'Uvoz „{uvoz.naziv}” sačuvan: {result["updated"]} ažurirano, '
                        f'{result["created"]} kreirano.',
                    )
                    if result.get('errors'):
                        messages.warning(
                            request,
                            f'{len(result["errors"])} greška(ka) — vidi detalje uvoza.',
                        )
                    return redirect('staff_uvoz_detail', pk=uvoz.pk)
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception as exc:
                logger.exception('Uvoz Excel nije uspio')
                messages.error(request, f'Uvoz nije uspio: {exc}')

    uvozi = (
        Uvoz.objects.filter(izvor=Uvoz.Izvor.SAJT)
        .select_related('kreirao')
        .annotate(stavke_n=Count('stavke'))
        .order_by('-kreiran')[:100]
    )
    context = {
        **_base_context(),
        'uvozi': uvozi,
        'result': result,
        'dry_run': dry_run,
        'search_q': search_q,
        'search_results': search_results,
    }
    return render(request, 'staff/uvoz.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_uvoz_detail(request, pk):
    """Pregled / izmjena stavki uvoza / brisanje / re-apply."""
    from .models import Uvoz, UvozStavka
    from .uvoz_import import parse_money, parse_qty, reapply_stavka

    uvoz = get_object_or_404(
        Uvoz.objects.select_related('kreirao'),
        pk=pk,
        izvor=Uvoz.Izvor.SAJT,
    )
    stavke = list(uvoz.stavke.select_related('product').all())

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'delete_uvoz':
            name = uvoz.naziv
            uvoz.delete()
            messages.success(request, f'Uvoz „{name}” je obrisan.')
            return redirect('staff_uvoz')

        if action == 'save_meta':
            uvoz.naziv = (request.POST.get('naziv') or uvoz.naziv).strip()[:200] or uvoz.naziv
            uvoz.napomena = (request.POST.get('napomena') or '').strip()
            uvoz.save(update_fields=['naziv', 'napomena', 'azuriran'])
            messages.success(request, 'Naziv i napomena sačuvani.')
            return redirect('staff_uvoz_detail', pk=uvoz.pk)

        if action == 'delete_stavka':
            try:
                sid = int(request.POST.get('stavka_id') or 0)
            except (TypeError, ValueError):
                sid = 0
            deleted, _ = UvozStavka.objects.filter(pk=sid, uvoz=uvoz).delete()
            if deleted:
                uvoz.broj_redova = uvoz.stavke.count()
                uvoz.save(update_fields=['broj_redova', 'azuriran'])
                messages.success(request, 'Stavka obrisana.')
            else:
                messages.error(request, 'Stavka nije pronađena.')
            return redirect('staff_uvoz_detail', pk=uvoz.pk)

        if action == 'save_stavka':
            try:
                sid = int(request.POST.get('stavka_id') or 0)
            except (TypeError, ValueError):
                sid = 0
            stavka = UvozStavka.objects.filter(pk=sid, uvoz=uvoz).first()
            if not stavka:
                messages.error(request, 'Stavka nije pronađena.')
                return redirect('staff_uvoz_detail', pk=uvoz.pk)

            stavka.artikal_naziv = (request.POST.get('artikal_naziv') or stavka.artikal_naziv).strip()[:200]
            stavka.kolicina = parse_qty(request.POST.get('kolicina'))
            stavka.mpc_brutto = parse_money(request.POST.get('mpc_brutto'))
            stavka.fakturna = parse_money(request.POST.get('fakturna'))
            stavka.nabavna = parse_money(request.POST.get('nabavna'))
            stavka.vpc_netto = parse_money(request.POST.get('vpc_netto'))
            stavka.ukupno_fakturna = parse_money(request.POST.get('ukupno_fakturna'))
            # Vpc marža: unos u % (npr. 69.18) ili udeo (0.69)
            raw_marza = (request.POST.get('vpc_marza') or '').strip()
            if raw_marza == '':
                stavka.vpc_marza = None
            else:
                marza_val = parse_qty(raw_marza.replace('%', ''))
                if marza_val is not None and marza_val > 2:
                    # uneseno kao % → snimi kao udeo radi konzistentnosti s Excelom
                    from decimal import Decimal
                    stavka.vpc_marza = (marza_val / Decimal('100'))
                else:
                    stavka.vpc_marza = marza_val
            stavka.save()

            apply_now = (request.POST.get('apply') or '').strip() in ('1', 'true', 'on', 'yes')
            if apply_now:
                reapply_stavka(stavka)
                messages.success(request, f'Stavka sačuvana i primijenjena: {stavka.poruka}')
            else:
                messages.success(request, 'Stavka sačuvana (nije primijenjena na sajt).')
            return redirect('staff_uvoz_detail', pk=uvoz.pk)

        if action == 'reapply_all':
            ok = 0
            err = 0
            for stavka in uvoz.stavke.all():
                if stavka.kolicina is None or stavka.kolicina <= 0:
                    continue
                if not stavka.mpc_brutto or stavka.mpc_brutto <= 0:
                    continue
                reapply_stavka(stavka)
                if stavka.status == UvozStavka.Status.ERROR:
                    err += 1
                else:
                    ok += 1
            uvoz.broj_azurirano = uvoz.stavke.filter(status=UvozStavka.Status.UPDATED).count()
            uvoz.broj_kreirano = uvoz.stavke.filter(status=UvozStavka.Status.CREATED).count()
            uvoz.save(update_fields=['broj_azurirano', 'broj_kreirano', 'azuriran'])
            messages.success(request, f'Ponovo primijenjeno: {ok} stavki' + (f', grešaka {err}' if err else '') + '.')
            return redirect('staff_uvoz_detail', pk=uvoz.pk)

        if action == 'reapply_stavka':
            try:
                sid = int(request.POST.get('stavka_id') or 0)
            except (TypeError, ValueError):
                sid = 0
            stavka = UvozStavka.objects.filter(pk=sid, uvoz=uvoz).first()
            if not stavka:
                messages.error(request, 'Stavka nije pronađena.')
            else:
                reapply_stavka(stavka)
                messages.success(request, f'Primijenjeno: {stavka.poruka}')
            return redirect('staff_uvoz_detail', pk=uvoz.pk)

    context = {
        **_base_context(),
        'uvoz': uvoz,
        'stavke': stavke,
        'status_counts': {
            'created': sum(1 for s in stavke if s.status == 'created'),
            'updated': sum(1 for s in stavke if s.status == 'updated'),
            'skipped': sum(1 for s in stavke if s.status == 'skipped'),
            'error': sum(1 for s in stavke if s.status == 'error'),
            'pending': sum(1 for s in stavke if s.status == 'pending'),
        },
    }
    return render(request, 'staff/uvoz_detail.html', context)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
def staff_site_overview(request):
    """
    Lagani pregled: posjetioci / kupovine / izvori.
    period=day|month|year|range (+ from/to datumi). Bez teških all-time upita.
    """
    from .site_stats import build_site_overview

    period = (request.GET.get('period') or 'day').strip().lower()
    date_from = (request.GET.get('from') or request.GET.get('date_from') or '').strip()
    date_to = (request.GET.get('to') or request.GET.get('date_to') or '').strip()
    data = build_site_overview(
        period=period,
        date_from=date_from,
        date_to=date_to,
    )
    context = {
        **_base_context(),
        **data,
    }
    return render(request, 'staff/site_overview.html', context)


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
    """
    Lagani live context — samo online kupci, šta rade, odakle dolaze.
    Bez gift/registered/traffic/AI/korpa mapa (CPU).
    """
    from django.core.cache import cache
    from django.utils import timezone as dj_tz

    from .live_visitors import get_live_visitor_snapshot_lite

    # Kratki cache 3s — poll svakih 5s ne udara DB dva puta u istom trenutku
    cache_key = 'staff_live_snapshot_lite_v1'
    snapshot = cache.get(cache_key)
    if snapshot is None:
        snapshot = get_live_visitor_snapshot_lite()
        cache.set(cache_key, snapshot, 3)

    generated_at = snapshot.get('generated_at') or dj_tz.now()
    if hasattr(generated_at, 'astimezone'):
        generated_at = dj_tz.localtime(generated_at)
    online_visitors = snapshot.get('online_visitors') or []
    return {
        'online_count': snapshot.get('online_count') or len(online_visitors),
        'online_visitors': online_visitors,
        'sources': snapshot.get('sources') or [],
        'online_minutes': snapshot.get('online_minutes') or 1,
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
    payload['csrf_token'] = request.META.get('CSRF_COOKIE') or ''
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

    payload['csrf_token'] = request.META.get('CSRF_COOKIE') or ''
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
    """JSON poll — lagani payload (online lista + izvori)."""
    from django.utils import timezone

    payload = _live_analytics_context(request)
    generated = payload.get('generated_at')
    if generated and hasattr(generated, 'isoformat'):
        payload['generated_at'] = timezone.localtime(generated).isoformat()
    # Samo potrebna polja klijentu
    slim = {
        'online_count': payload.get('online_count') or 0,
        'online_visitors': payload.get('online_visitors') or [],
        'sources': payload.get('sources') or [],
        'online_minutes': payload.get('online_minutes') or 1,
        'generated_at': payload.get('generated_at'),
        'generated_at_label': payload.get('generated_at_label') or '',
    }
    response = JsonResponse(slim)
    response['Cache-Control'] = 'private, max-age=2'
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
    if raw in ('zavrsene', 'zavrsena', 'validirane', 'validirana'):
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
        elif action == 'odoo_narudzba' and broj:
            _create_odoo_sale_order_from_web(request, broj)
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
        from .magacin import refresh_catalog_qty
        refresh_catalog_qty(product)
        product.refresh_from_db(fields=['na_stanju', 'stanje'])
        return _staff_product_edit_redirect(request, slug)
    elif action == 'hide_until_stock':
        product.sakriven_do_stanja = not product.sakriven_do_stanja
        product.save(update_fields=['sakriven_do_stanja'])
        _invalidate_storefront_product_caches()
        if product.sakriven_do_stanja:
            messages.success(
                request,
                'Artikal je sakriven sa sajta. Kad opet dođe na stanje, prikazat će se automatski.',
            )
        else:
            messages.success(request, 'Artikal je ponovo vidljiv na sajtu.')
        return _staff_product_edit_redirect(request, slug)
    elif action == 'toggle_japan':
        product.proizvedeno_u_japanu = not product.proizvedeno_u_japanu
        product.save(update_fields=['proizvedeno_u_japanu'])
        return _staff_product_edit_redirect(request, slug)
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
            # Greška → ostani na artiklu da ispraviš
            return redirect('product_detail', slug=slug)

        product.save()
        # Dodatne slike (opcionalno u istom save-u)
        uploads = request.FILES.getlist('dodatne_slike')
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
        # Tagovi (chipovi + ručni unos zarezom)
        tags_touched = (
            'tag_ids' in request.POST
            or 'tag_names' in request.POST
            or 'tagovi_tekst' in request.POST
            or request.POST.get('set_tags_with_save')
        )
        if tags_touched:
            tags = _staff_resolve_product_tags(request)
            product.tagovi.set(tags)
            changed.append('tagovi')

        return _staff_product_edit_redirect(request, slug)
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
        return _staff_product_edit_redirect(request, slug)
    elif action == 'set_category':
        raw_id = (request.POST.get('kategorija_id') or '').strip()
        if not raw_id:
            product.kategorija = None
            product.save(update_fields=['kategorija'])
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
        return _staff_product_edit_redirect(request, slug)
    elif action == 'set_brand':
        raw_id = (request.POST.get('brend_id') or '').strip()
        if not raw_id:
            product.brend = None
            product.save(update_fields=['brend'])
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
        return _staff_product_edit_redirect(request, slug)
    elif action == 'set_opis':
        product.opis = (request.POST.get('opis') or '').strip()
        product.save(update_fields=['opis'])
        return _staff_product_edit_redirect(request, slug)
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
        return _staff_product_edit_redirect(request, slug)
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
        return _staff_product_edit_redirect(request, slug)
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
        return _staff_product_edit_redirect(request, slug)
    elif action == 'set_tags':
        tags = _staff_resolve_product_tags(request)
        product.tagovi.set(tags)
        return _staff_product_edit_redirect(request, slug)
    elif action == 'activate_akcija':
        # JSON: iz objave (Akcija) — postavi isti % popust + opcionalni rok
        wants_json = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in (request.headers.get('Accept') or '')
        )

        def _akcija_json(ok, message, **extra):
            status = 200 if ok else 400
            payload = {'ok': ok, 'message': message, **extra}
            return JsonResponse(payload, status=status)

        raw_pct = (request.POST.get('akcija_postotak') or request.POST.get('percent') or '').strip().replace(',', '.')
        try:
            pct = Decimal(raw_pct)
        except (InvalidOperation, ValueError, TypeError):
            msg = 'Unesi ispravan popust % (npr. 15 ili 20).'
            if wants_json:
                return _akcija_json(False, msg)
            messages.error(request, msg)
            return redirect('product_detail', slug=slug)
        if pct <= 0 or pct >= 100:
            msg = 'Popust mora biti između 0 i 100 %.'
            if wants_json:
                return _akcija_json(False, msg)
            messages.error(request, msg)
            return redirect('product_detail', slug=slug)

        # Trajanje: samo sati ILI samo datum (ne oba obavezno; prazno = bez roka)
        akcija_do = None
        raw_hours = (
            request.POST.get('akcija_sati')
            or request.POST.get('hours')
            or request.POST.get('akcija_dana')  # legacy
            or ''
        ).strip()
        raw_date = (request.POST.get('akcija_do') or '').strip()
        if raw_date and raw_hours:
            # preferiraj ono što je eksplicitno poslato kao primarno — UI šalje jedno
            # ako oba dođu, datum ima prioritet samo ako hours prazan; ovdje: sati prvi
            pass
        if raw_hours and not raw_date:
            try:
                hours = int(raw_hours)
            except (TypeError, ValueError):
                hours = 0
            if hours > 0:
                from datetime import timedelta
                # DateField: krajnji dan = dan kada sati isteknu
                end_dt = timezone.now() + timedelta(hours=hours)
                akcija_do = timezone.localtime(end_dt).date()
        elif raw_date:
            try:
                from datetime import date as date_cls
                akcija_do = date_cls.fromisoformat(raw_date)
            except ValueError:
                msg = 'Datum trajanja akcije nije ispravan (YYYY-MM-DD).'
                if wants_json:
                    return _akcija_json(False, msg)
                messages.error(request, msg)
                return redirect('product_detail', slug=slug)

        product.akcija_postotak = pct.quantize(Decimal('0.01'))
        product.akcijska_cijena = None  # save() računa iz postotka
        product.akcija_do = akcija_do
        product.save()
        product.refresh_from_db()
        sale = product.akcijska_cijena or product.prikazna_cijena
        if raw_hours and not raw_date and akcija_do:
            msg = (
                f'Akcija aktivirana na „{product.naziv}”: −{product.akcija_postotak}% '
                f'({sale} KM), oko {raw_hours} h (do {akcija_do.strftime("%d.%m.%Y.")}).'
            )
        elif akcija_do:
            msg = (
                f'Akcija aktivirana na „{product.naziv}”: −{product.akcija_postotak}% '
                f'({sale} KM), važi do {akcija_do.strftime("%d.%m.%Y.")}.'
            )
        else:
            msg = (
                f'Akcija aktivirana na „{product.naziv}”: −{product.akcija_postotak}% '
                f'({sale} KM), bez roka.'
            )
        if wants_json:
            return _akcija_json(
                True,
                msg,
                akcija_postotak=str(product.akcija_postotak),
                akcijska_cijena=str(sale),
                akcija_do=akcija_do.isoformat() if akcija_do else None,
                prikazna_cijena=str(product.prikazna_cijena),
            )
        return _staff_product_edit_redirect(request, slug)
    elif action == 'deactivate_akcija':
        product.akcija_postotak = None
        product.akcijska_cijena = None
        product.akcija_do = None
        product.save(update_fields=['akcija_postotak', 'akcijska_cijena', 'akcija_do'])
        return _staff_product_edit_redirect(request, slug)
    else:
        messages.error(request, 'Nepoznata akcija.')
    return redirect('product_detail', slug=slug)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_POST
def staff_product_bulk_edit(request):
    if not _staff_edit_mode_enabled(request):
        return JsonResponse({'ok': False, 'error': 'Edit mode je isključen.'}, status=403)
    if 'application/json' in (request.content_type or ''):
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except (TypeError, ValueError, json.JSONDecodeError):
            return JsonResponse({'ok': False, 'error': 'Neispravan zahtjev.'}, status=400)
        if not isinstance(payload, dict):
            return JsonResponse({'ok': False, 'error': 'Neispravan zahtjev.'}, status=400)
        raw_ids = payload.get('product_ids') or []
    else:
        payload = request.POST
        raw_ids = request.POST.getlist('product_ids')
    ids = _parse_staff_product_ids(raw_ids)
    if not ids:
        return JsonResponse({'ok': False, 'error': 'Odaberi artikle.'}, status=400)

    updates = {}
    raw_cat = str(payload.get('kategorija_id') or '').strip()
    if raw_cat:
        try:
            category = Category.objects.filter(pk=int(raw_cat)).first()
        except (TypeError, ValueError):
            category = None
        if category is None:
            return JsonResponse({'ok': False, 'error': 'Kategorija nije pronađena.'}, status=400)
        updates['kategorija'] = category

    raw_brand = str(payload.get('brend_id') or '').strip()
    if raw_brand:
        try:
            brand = Brand.objects.filter(pk=int(raw_brand)).first()
        except (TypeError, ValueError):
            brand = None
        if brand is None:
            return JsonResponse({'ok': False, 'error': 'Brend nije pronađen.'}, status=400)
        updates['brend'] = brand

    if 'opis' in payload:
        opis = str(payload.get('opis') or '').strip()
        if opis:
            updates['opis'] = opis

    raw_pct = str(payload.get('akcija_postotak') or '').strip().replace(',', '.')
    if raw_pct:
        try:
            pct = Decimal(raw_pct)
        except (InvalidOperation, ValueError):
            return JsonResponse({'ok': False, 'error': 'Unesi ispravan akcijski % (npr. 15).'}, status=400)
        if pct < 0 or pct >= 100:
            return JsonResponse({'ok': False, 'error': 'Akcijski % mora biti između 0 i 100.'}, status=400)
        if pct == 0:
            updates['akcija_postotak'] = None
            updates['akcijska_cijena'] = None
        else:
            updates['akcija_postotak'] = pct.quantize(Decimal('0.01'))
            updates['akcijska_cijena'] = None

    raw_hit = str(payload.get('je_hit') or '').strip().lower()
    if raw_hit in ('1', 'true', 'on', 'da', 'yes'):
        updates['je_hit'] = True
    elif raw_hit in ('0', 'false', 'off', 'ne', 'no'):
        updates['je_hit'] = False

    raw_hide = str(
        payload.get('sakriven_do_stanja') or payload.get('hide_from_site') or '',
    ).strip().lower()
    if raw_hide in ('1', 'true', 'on', 'da', 'yes', 'hide', 'sakrij'):
        updates['sakriven_do_stanja'] = True
    elif raw_hide in ('0', 'false', 'off', 'ne', 'no', 'show', 'prikazi', 'prikaži'):
        updates['sakriven_do_stanja'] = False

    per_opis = {}
    per_slika = {}
    files = getattr(request, 'FILES', None)
    for pk in ids:
        raw_item_opis = str(payload.get(f'opis_{pk}') or '').strip()
        if raw_item_opis:
            per_opis[pk] = raw_item_opis
        uploaded = files.get(f'slika_{pk}') if files is not None else None
        if not uploaded:
            continue
        if not _staff_upload_is_image(uploaded):
            return JsonResponse({'ok': False, 'error': 'Slika mora biti slika.'}, status=400)
        per_slika[pk] = uploaded

    extra_uploads = []
    if files is not None:
        for uploaded in files.getlist('dodatne_slike'):
            if not uploaded:
                continue
            if not _staff_upload_is_image(uploaded):
                return JsonResponse({'ok': False, 'error': 'Dodatna slika mora biti slika.'}, status=400)
            extra_uploads.append(uploaded)
            if len(extra_uploads) >= 12:
                break

    if not updates and not per_opis and not per_slika and not extra_uploads:
        return JsonResponse({'ok': False, 'error': 'Unesi barem jedno polje.'}, status=400)

    products = list(Product.objects.filter(pk__in=ids))
    if not products:
        return JsonResponse({'ok': False, 'error': 'Artikli nisu pronađeni.'}, status=400)
    for product in products:
        for field, value in updates.items():
            setattr(product, field, value)
        if product.pk in per_opis:
            product.opis = per_opis[product.pk]
        if product.pk in per_slika:
            product.slika = per_slika[product.pk]
        product.save()
        if extra_uploads:
            max_order = (
                product.dodatne_slike.aggregate(max_red=Max('redoslijed')).get('max_red') or 0
            )
            for index, uploaded in enumerate(extra_uploads, start=1):
                ProductImage.objects.create(
                    product=product,
                    slika=_clone_uploaded_image(uploaded),
                    redoslijed=max_order + index,
                )
    parts = []
    if 'kategorija' in updates:
        parts.append('kategorija')
    if 'brend' in updates:
        parts.append('brend')
    if 'opis' in updates or per_opis:
        parts.append('opis')
    if per_slika:
        parts.append('slika')
    if extra_uploads:
        parts.append(f'dodatne slike ({len(extra_uploads)})')
    if 'akcija_postotak' in updates:
        if updates['akcija_postotak'] is None:
            parts.append('akcija skinuta')
        else:
            parts.append(f'akcijski {updates["akcija_postotak"]}%')
    if 'je_hit' in updates:
        parts.append('HIT ponuda ' + ('uključeno' if updates['je_hit'] else 'isključeno'))
    if 'sakriven_do_stanja' in updates:
        parts.append(
            'sakriven sa sajta' if updates['sakriven_do_stanja'] else 'vraćen na sajt',
        )
        _invalidate_storefront_product_caches()
    payload = {
        'ok': True,
        'count': len(products),
        'message': f'Primijenjeno na {len(products)} artikal(a): {", ".join(parts)}.',
    }
    if 'sakriven_do_stanja' in updates:
        payload['hidden'] = bool(updates['sakriven_do_stanja'])
    return JsonResponse(payload)


@login_required(login_url='login')
@user_passes_test(_superuser_required)
@require_GET
def staff_same_image_products(request):
    """Artikli koji dijele istu glavnu sliku s odabranima — za bulk dodatne slike."""
    if not _staff_edit_mode_enabled(request):
        return JsonResponse({'ok': False, 'error': 'Edit mode je isključen.'}, status=403)
    ids = _parse_staff_product_ids(request.GET.getlist('product_ids'))
    if not ids:
        return JsonResponse({'ok': False, 'error': 'Odaberi artikle.'}, status=400)
    image_names = [
        name for name in (
            Product.objects.filter(pk__in=ids)
            .exclude(slika='')
            .exclude(slika__isnull=True)
            .values_list('slika', flat=True)
        )
        if name
    ]
    if not image_names:
        return JsonResponse({
            'ok': False,
            'error': 'Odabrani artikli nemaju glavnu sliku.',
        }, status=400)
    matches = list(
        Product.objects.filter(slika__in=image_names)
        .order_by('naziv', 'id')
        .values('id', 'naziv')[:200]
    )
    return JsonResponse({
        'ok': True,
        'count': len(matches),
        'results': [{'id': row['id'], 'label': row['naziv']} for row in matches],
    })


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
    from django.urls import reverse
    from .loyalty import (
        azuriraj_loyalty_karticu,
        clear_pending_open_card_otp,
        format_ba_int,
        format_ba_money,
        format_loyalty_phone,
        get_pending_open_card_otp,
        izdaj_loyalty_karticu,
        loyalty_card_share_token,
        loyalty_desk_purchase_ledger,
        loyalty_desk_stats,
        loyalty_desk_url,
        loyalty_from_phone_display,
        loyalty_kontekst,
        loyalty_member_url,
        loyalty_page_items,
        loyalty_phone_local_display,
        osiguraj_loyalty_karticu,
        osiguraj_sestocifreni_kod,
        recent_loyalty_cards,
        search_loyalty_cards,
        start_open_card_otp,
        verify_open_card_otp,
    )

    from .models import LoyaltyPurchase

    def _loyalty_ctx(card):
        """Loyalty kontekst s javnim URL-om slike (za WhatsApp/Viber)."""
        token = loyalty_card_share_token(card)
        share_url = ''
        if token:
            path = reverse(
                'public_loyalty_card_image',
                kwargs={'card_id': card.pk, 'token': token},
            )
            share_url = request.build_absolute_uri(path)
        return loyalty_kontekst(card, share_image_url=share_url)

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
            return redirect(loyalty_desk_url(
                request.path, q=q,
                mode=request.POST.get('mode') or request.GET.get('mode') or 'code',
                nivo=request.POST.get('nivo') or request.GET.get('nivo') or '',
            ))
        return redirect(request.path)

    if request.method == 'POST' and request.POST.get('action') == 'izdaj_karticu':
        issue_form = LoyaltyIssueForm(request.POST)
        if issue_form.is_valid():
            try:
                card, user = izdaj_loyalty_karticu(
                    issue_form.cleaned_data['ime'],
                    issue_form.cleaned_data['prezime'],
                    issue_form.cleaned_data['telefon'],
                    issue_form.cleaned_data.get('email') or '',
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                sync_korisnik(user)
                messages.success(
                    request,
                    f'Kartica izdata za {user.get_full_name()}. Broj: {card.kod}',
                )
                return redirect(loyalty_member_url(card.kod))
        else:
            messages.error(request, 'Provjerite unesene podatke (dupli email/telefon nisu dozvoljeni).')

    if request.method == 'POST' and request.POST.get('action') == 'open_card':
        phone = (request.POST.get('telefon') or '').strip()
        channel = (request.POST.get('channel') or 'admin').strip().lower()
        ime = (request.POST.get('ime') or '').strip()
        prezime = (request.POST.get('prezime') or '').strip()
        wants_json = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.POST.get('ajax') == '1'
        )
        creating = bool(ime and prezime)
        strani = (request.POST.get('strani') or '').strip().lower() in ('1', 'on', 'true', 'da')

        def _open_json(payload, status=200):
            return JsonResponse(payload, status=status)

        if creating:
            from .loyalty import telefon_vec_registrovan, validiraj_loyalty_telefon
            try:
                phone, _e164 = validiraj_loyalty_telefon(phone, strani=strani)
            except ValueError as exc:
                messages.error(request, str(exc))
                dest = loyalty_desk_url(request.path, extra={'novi': '1', 'tel': phone})
                if wants_json:
                    return _open_json({'ok': False, 'error': str(exc), 'redirect': dest}, 400)
                return redirect(dest)
            if telefon_vec_registrovan(phone):
                err = 'Ovaj broj telefona je već registrovan — isti telefon nije dozvoljen.'
                messages.error(request, err)
                dest = loyalty_desk_url(request.path, extra={'novi': '1', 'tel': phone})
                if wants_json:
                    return _open_json({'ok': False, 'error': err}, 400)
                return redirect(dest)
            try:
                card, user = izdaj_loyalty_karticu(ime, prezime, phone, strani=strani)
                sync_korisnik(user)
            except ValueError as exc:
                messages.error(request, str(exc))
                dest = loyalty_desk_url(request.path, extra={'novi': '1', 'tel': phone})
                if wants_json:
                    return _open_json({'ok': False, 'error': str(exc), 'redirect': dest}, 400)
                return redirect(dest)
        else:
            found = search_loyalty_cards(phone, limit=5, mode='code') if phone else []
            card = found[0] if found else None
            if card is None:
                from .loyalty import validiraj_loyalty_telefon
                try:
                    phone, _e164 = validiraj_loyalty_telefon(phone, strani=strani)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    if wants_json:
                        return _open_json({'ok': False, 'error': str(exc)}, 400)
                    return redirect(request.path)
                messages.info(request, 'Novi član — unesi ime i prezime pa ponovo pošalji kod ili Admin otvori.')
                request.session['loyalty_new_phone'] = phone
                request.session.modified = True
                dest = loyalty_desk_url(request.path, extra={'novi': '1', 'tel': phone})
                if wants_json:
                    return _open_json({'ok': False, 'need_name': True, 'redirect': dest})
                return redirect(dest)
        if channel == 'admin':
            clear_pending_open_card_otp(request)
            request.session.pop('loyalty_new_phone', None)
            dest = loyalty_member_url(card.kod)
            if wants_json:
                return _open_json({'ok': True, 'redirect': dest})
            return redirect(dest)
        try:
            otp_info = start_open_card_otp(request, card, channel=channel)
        except ValueError as exc:
            messages.error(request, str(exc))
            if wants_json:
                return _open_json({'ok': False, 'error': str(exc)}, 400)
            return redirect(request.path)
        dest = loyalty_desk_url(request.path, extra={'step': '2'})
        if wants_json:
            chat_url = (
                otp_info.get('whatsapp_url')
                if channel == 'whatsapp'
                else otp_info.get('viber_url')
            )
            return _open_json({
                'ok': True,
                'chat_url': chat_url or '',
                'app_url': (
                    otp_info.get('whatsapp_app_url')
                    if channel == 'whatsapp'
                    else otp_info.get('viber_url')
                ) or '',
                'redirect': dest,
            })
        return redirect(dest)

    if request.method == 'POST' and request.POST.get('action') == 'open_card_verify':
        ok, result = verify_open_card_otp(request, request.POST.get('otp_code'))
        if not ok:
            messages.error(request, result)
            return redirect(loyalty_desk_url(request.path, extra={'step': '2'}))
        kod = result.get('kod') or ''
        clear_pending_open_card_otp(request)
        if kod:
            return redirect(loyalty_member_url(kod))
        return redirect(request.path)

    if request.method == 'POST' and request.POST.get('action') == 'open_card_admin':
        pending_open = get_pending_open_card_otp(request) or {}
        kod = pending_open.get('kod') or ''
        clear_pending_open_card_otp(request)
        if kod:
            return redirect(loyalty_member_url(kod))
        return redirect(request.path)

    if request.method == 'POST' and request.POST.get('action') == 'open_card_cancel':
        clear_pending_open_card_otp(request)
        return redirect(request.path)

    q = (request.GET.get('q') or '').strip()
    search_mode = (request.GET.get('mode') or 'code').strip().lower()
    if search_mode not in {'code', 'name', 'any'}:
        search_mode = 'code'
    search_nivo = (request.GET.get('nivo') or '').strip().lower()
    valid_nivoi = {choice.value for choice in LoyaltyCard.Nivo}
    if search_nivo not in valid_nivoi:
        search_nivo = ''
    cards = []
    selected_card = None
    user_orders = []
    purchase_timeline = []
    loyalty_ctx = None
    edit_form = None
    cardholder_name = ''
    pending_otp = None
    searched = bool(q)

    if q:
        cards = search_loyalty_cards(q, limit=30, mode=search_mode)
        if search_nivo:
            cards = [card for card in cards if card.nivo == search_nivo]

        if cards:
            only = cards[0]
            qn = q.replace(' ', '').casefold()
            if len(cards) == 1 and qn in {
                (only.kod or '').casefold(),
                (only.barkod or '').casefold(),
            }:
                return redirect(loyalty_member_url(only.kod))
            selected_card = None
        if False:
            selected_card = cards[0]
            selected_card = osiguraj_loyalty_karticu(selected_card.user)
            loyalty_ctx = _loyalty_ctx(selected_card)
            cardholder_name = (
                selected_card.user.get_full_name().strip()
                or (selected_card.user.email or '').strip().lower()
            )

            from .loyalty import online_orders_for_loyalty_card

            # Online (i gost) narudžbe po nalogu / emailu / telefonu — bez obaveznog koda
            user_orders = online_orders_for_loyalty_card(selected_card, limit=50)
            manual_purchases = list(
                LoyaltyPurchase.objects.filter(kartica=selected_card)
                .select_related('kreirao')
                .order_by('-kreirano')[:50]
            )

            # Timeline: online narudžbe + evidentirane kupovine
            for order in user_orders:
                purchase_timeline.append({
                    'kind': 'online',
                    'date': order.kreirana,
                    'amount': order.ukupno,
                    'label': f'#{order.broj}',
                    'status': order.get_status_label() if hasattr(order, 'get_status_label') else order.status,
                    'status_code': order.status,
                    'order': order,
                    'note': '',
                })
            for pur in manual_purchases:
                if getattr(pur, 'verifikacija', '') == LoyaltyPurchase.Verifikacija.ADMIN:
                    status_label = 'Prodavnica · admin (bez koda)'
                else:
                    status_label = 'Prodavnica · kod'
                purchase_timeline.append({
                    'kind': 'manual',
                    'date': pur.kreirano,
                    'amount': pur.iznos,
                    'label': 'Evidentirano',
                    'status': status_label,
                    'status_code': 'manual',
                    'order': None,
                    'note': pur.napomena or '',
                    'purchase': pur,
                })
            from django.utils import timezone as dj_tz
            purchase_timeline.sort(
                key=lambda row: row['date'] or dj_tz.now(),
                reverse=True,
            )

            profil = getattr(selected_card.user, 'profil', None)

            from .loyalty import (
                clear_pending_purchase_otp,
                commit_loyalty_purchase,
                get_pending_purchase_otp,
                start_purchase_otp,
                verify_purchase_otp,
            )

            # Anchor da ostanemo na „Evidentiraj kupovinu” (ne skrola na vrh)
            _purchase_anchor = '#evidentiraj-kupovinu'

            def _wants_json():
                return (
                    request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or request.POST.get('ajax') == '1'
                )

            # 1) Start: Viber/WhatsApp → OTP; Admin → odmah evidentiraj
            if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_start':
                wants_json = _wants_json()
                channel = (request.POST.get('channel') or 'whatsapp').strip().lower()
                if channel not in ('viber', 'whatsapp', 'admin'):
                    channel = 'whatsapp'
                try:
                    iznos = Decimal(request.POST.get('iznos', '0'))
                    napomena = (request.POST.get('napomena') or '').strip()[:200]
                except (InvalidOperation, TypeError, ValueError):
                    if wants_json:
                        return JsonResponse({'ok': False, 'message': 'Neispravan iznos.'}, status=400)
                    messages.error(request, 'Neispravan iznos.')
                    return redirect(f"{request.path}?q={q}{_purchase_anchor}")

                # Admin: odmah evidentiraj, bez koda i bez potvrde
                if channel == 'admin':
                    try:
                        purchase = commit_loyalty_purchase(
                            selected_card,
                            iznos,
                            napomena=napomena or 'Admin — nema internet (bez koda)',
                            verifikacija=LoyaltyPurchase.Verifikacija.ADMIN,
                            staff_user=request.user,
                        )
                        clear_pending_purchase_otp(request)
                        redirect_url = f"{request.path}?q={q}{_purchase_anchor}"
                        if wants_json:
                            return JsonResponse({
                                'ok': True,
                                'admin': True,
                                'redirect': redirect_url,
                                'message': f'Kupovina od {purchase.iznos} KM evidentirana (admin).',
                            })
                        messages.warning(
                            request,
                            f'Kupovina od {purchase.iznos} KM evidentirana BEZ koda (admin).',
                        )
                        return redirect(redirect_url)
                    except ValueError as exc:
                        if wants_json:
                            return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
                        messages.error(request, str(exc))
                        return redirect(f"{request.path}?q={q}{_purchase_anchor}")

                # Viber / WhatsApp: generiši kod
                try:
                    otp_info = start_purchase_otp(request, selected_card, iznos, napomena)
                    # zapamti kanal u sesiji (za UI)
                    pending = get_pending_purchase_otp(request, card=selected_card) or {}
                    pending['channel'] = channel
                    from .loyalty import LOYALTY_PURCHASE_OTP_SESSION_KEY
                    request.session[LOYALTY_PURCHASE_OTP_SESSION_KEY] = pending
                    request.session.modified = True

                    open_q = 'wa' if channel == 'whatsapp' else 'viber'
                    redirect_url = (
                        f"{request.path}?q={q}&otp=1&open={open_q}{_purchase_anchor}"
                    )
                    if wants_json:
                        return JsonResponse({
                            'ok': True,
                            'admin': False,
                            'channel': channel,
                            'redirect': redirect_url,
                            'iznos': str(otp_info['iznos']),
                            'telefon': otp_info.get('telefon') or '',
                            'message': otp_info.get('message') or '',
                            'whatsapp_url': otp_info.get('whatsapp_url') or '',
                            'viber_url': otp_info.get('viber_url') or '',
                            'sms_url': otp_info.get('sms_url') or '',
                        })
                    return redirect(redirect_url)
                except ValueError as exc:
                    if wants_json:
                        return JsonResponse({'ok': False, 'message': str(exc)}, status=400)
                    messages.error(request, str(exc))
                    return redirect(f"{request.path}?q={q}{_purchase_anchor}")

            # 2) Potvrda kodom
            if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_potvrdi':
                code = (request.POST.get('otp_code') or '').strip()
                ok, result = verify_purchase_otp(request, code, selected_card)
                if not ok:
                    messages.error(request, result)
                    return redirect(f"{request.path}?q={q}&otp=1{_purchase_anchor}")
                try:
                    purchase = commit_loyalty_purchase(
                        selected_card,
                        result.get('iznos'),
                        napomena=result.get('napomena') or '',
                        verifikacija=LoyaltyPurchase.Verifikacija.OTP,
                        staff_user=request.user,
                    )
                    clear_pending_purchase_otp(request)
                    selected_card = osiguraj_loyalty_karticu(selected_card.user)
                    loyalty_ctx = _loyalty_ctx(selected_card)
                    messages.success(
                        request,
                        f'Kupovina od {purchase.iznos} KM evidentirana (potvrđeno kodom).',
                    )
                    return redirect(f"{request.path}?q={q}{_purchase_anchor}")
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect(f"{request.path}?q={q}&otp=1{_purchase_anchor}")

            # 3) Admin override (iz OTP panela ili direktno)
            if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_admin':
                try:
                    pending = get_pending_purchase_otp(request, card=selected_card)
                    if pending:
                        iznos = Decimal(str(pending.get('iznos') or '0'))
                        napomena = (pending.get('napomena') or '').strip()[:200]
                    else:
                        iznos = Decimal(request.POST.get('iznos', '0'))
                        napomena = (request.POST.get('napomena') or '').strip()[:200]
                    if iznos <= 0:
                        messages.error(request, 'Iznos mora biti veći od 0.')
                        return redirect(f"{request.path}?q={q}{_purchase_anchor}")
                    purchase = commit_loyalty_purchase(
                        selected_card,
                        iznos,
                        napomena=napomena or 'Admin — nema internet (bez koda)',
                        verifikacija=LoyaltyPurchase.Verifikacija.ADMIN,
                        staff_user=request.user,
                    )
                    clear_pending_purchase_otp(request)
                    selected_card = osiguraj_loyalty_karticu(selected_card.user)
                    loyalty_ctx = _loyalty_ctx(selected_card)
                    messages.warning(
                        request,
                        f'Kupovina od {purchase.iznos} KM evidentirana BEZ koda (admin).',
                    )
                    return redirect(f"{request.path}?q={q}{_purchase_anchor}")
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect(f"{request.path}?q={q}{_purchase_anchor}")
                except (InvalidOperation, TypeError):
                    messages.error(request, 'Neispravan iznos.')
                    return redirect(f"{request.path}?q={q}{_purchase_anchor}")

            # 4) Otkaži pending OTP
            if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_cancel':
                clear_pending_purchase_otp(request)
                return redirect(f"{request.path}?q={q}{_purchase_anchor}")

            if request.method == 'POST' and request.POST.get('action') == 'update_profile':
                edit_form = StaffLoyaltyProfileForm(
                    request.POST,
                    exclude_user_id=selected_card.user_id,
                )
                if edit_form.is_valid():
                    u = selected_card.user
                    # Nijedno polje nije obavezno — prazno = obriši / ostavi prazno
                    ime_prezime = (edit_form.cleaned_data.get('ime_prezime') or '').strip()
                    if ime_prezime:
                        parts = ime_prezime.split(maxsplit=1)
                        u.first_name = parts[0]
                        u.last_name = parts[1] if len(parts) > 1 else ''
                    else:
                        # Dozvoli prazno ime ako staff tako unese
                        u.first_name = u.first_name or ''
                        u.last_name = u.last_name or ''
                    new_email = (edit_form.cleaned_data.get('email') or '').strip().lower()
                    u.email = new_email
                    u.save(update_fields=['first_name', 'last_name', 'email'])

                    profil, _ = UserProfile.objects.get_or_create(user=u)
                    profil.telefon = (edit_form.cleaned_data.get('telefon') or '').strip()
                    profil.adresa = (edit_form.cleaned_data.get('adresa') or '').strip()
                    profil.grad = (edit_form.cleaned_data.get('grad') or '').strip()
                    profil.postanski_broj = (
                        edit_form.cleaned_data.get('postanski_broj') or ''
                    ).strip()
                    profil.save()

                    messages.success(request, 'Podaci su ažurirani.')
                    return redirect(f"{request.path}?q={q}")
                else:
                    messages.error(
                        request,
                        'Greška pri ažuriranju (provjeri dupli email/telefon).',
                    )
            else:
                initial = {
                    'ime_prezime': selected_card.user.get_full_name() or selected_card.user.first_name,
                    'email': selected_card.user.email or '',
                }
                if profil:
                    from .loyalty import ba_mobile_local
                    initial.update({
                        'telefon': ba_mobile_local(profil.telefon) or '',
                        'adresa': profil.adresa or '',
                        'grad': profil.grad or '',
                        'postanski_broj': profil.postanski_broj or '',
                    })
                edit_form = StaffLoyaltyProfileForm(
                    initial=initial,
                    exclude_user_id=selected_card.user_id,
                )

            loyalty_ctx = _loyalty_ctx(selected_card)

            # Pending OTP UI (poslije svih POST redirecta)
            try:
                from .loyalty import (
                    get_pending_purchase_otp,
                    purchase_otp_message,
                    whatsapp_chat_url,
                    viber_chat_url,
                )
                raw_pending = get_pending_purchase_otp(request, card=selected_card)
                if raw_pending:
                    from .loyalty import sms_chat_url
                    code = raw_pending.get('code') or ''
                    iznos_p = raw_pending.get('iznos')
                    tel = raw_pending.get('telefon') or (
                        (getattr(selected_card.user, 'profil', None).telefon
                         if getattr(selected_card.user, 'profil', None) else '') or ''
                    )
                    msg = purchase_otp_message(code, iznos=iznos_p)
                    pending_otp = {
                        'iznos': iznos_p,
                        'napomena': raw_pending.get('napomena') or '',
                        'telefon': tel,
                        'message': msg,
                        'channel': raw_pending.get('channel') or request.GET.get('open') or '',
                        'viber_url': viber_chat_url(tel, msg),
                        'whatsapp_url': whatsapp_chat_url(tel, msg),
                        'sms_url': sms_chat_url(tel, msg),
                        'auto_open': request.GET.get('open') or '',
                    }
            except Exception:
                pending_otp = None

    desk_stats = loyalty_desk_stats()
    if searched:
        table_source = cards
    else:
        table_source = list(
            LoyaltyCard.objects.select_related('user', 'user__profil').order_by('-azurirana')
        )
    paginator = Paginator(table_source, 10)
    try:
        table_page = paginator.get_page(request.GET.get('page') or 1)
    except (EmptyPage, PageNotAnInteger):
        table_page = paginator.get_page(1)
    table_cards = list(table_page)
    _tier_en = {
        'bronza': 'BRONZE', 'srebrna': 'SILVER',
        'zlatna': 'GOLD', 'platinum': 'PLATINUM',
    }
    for card in table_cards:
        osiguraj_sestocifreni_kod(card)
        profil = getattr(card.user, 'profil', None)
        card.desk_phone = format_loyalty_phone(profil.telefon if profil else '')
        card.desk_url = loyalty_member_url(card.kod)
        card.desk_tier = _tier_en.get(card.nivo, (card.nivo or '').upper())
        card.desk_points = int(card.ukupna_potrosnja or 0)
        card.desk_points_fmt = format_ba_int(card.desk_points)
        card.desk_spend_fmt = format_ba_money(card.ukupna_potrosnja)
    pending_open = get_pending_open_card_otp(request)
    if pending_open:
        import time as _time
        from .loyalty import (
            LOYALTY_OPEN_OTP_TTL_SEC,
            loyalty_from_phone_display,
            open_card_otp_message,
            viber_chat_url,
            whatsapp_app_url,
            whatsapp_chat_url,
        )
        msg = open_card_otp_message(pending_open.get('code') or '')
        now = _time.time()
        sent_ts = float(pending_open.get('sent_ts') or (
            float(pending_open.get('exp') or now) - LOYALTY_OPEN_OTP_TTL_SEC
        ))
        resend_wait = max(0, int(60 - (now - sent_ts)))
        channel = (pending_open.get('channel') or request.GET.get('open') or 'viber').strip().lower()
        pending_open = {
            **pending_open,
            'message': msg,
            'viber_url': viber_chat_url(pending_open.get('telefon') or '', msg),
            'whatsapp_url': whatsapp_chat_url(pending_open.get('telefon') or '', msg),
            'whatsapp_app_url': whatsapp_app_url(pending_open.get('telefon') or '', msg),
            'telefon_fmt': format_loyalty_phone(pending_open.get('telefon') or ''),
            'from_phone_fmt': loyalty_from_phone_display(),
            'resend_wait': resend_wait,
            'channel_label': 'WhatsApp' if channel == 'whatsapp' else 'Viber',
        }
    page_items = loyalty_page_items(table_page) if table_page else []
    desk_tab = (request.GET.get('tab') or 'pretraga').strip().lower()
    if request.GET.get('novi') == '1':
        desk_tab = 'novi'
    if desk_tab not in {'pretraga', 'novi'}:
        desk_tab = 'pretraga'
    desk_ledger = loyalty_desk_purchase_ledger(
        year=request.GET.get('godina'),
        page=request.GET.get('ep') or 1,
    )
    raw_phone = ''
    if desk_tab == 'novi':
        raw_phone = request.GET.get('tel') or request.session.get('loyalty_new_phone') or ''
    phone_local = loyalty_phone_local_display(raw_phone)
    open_step = 3 if (request.GET.get('opened') == '1' and selected_card) else (
        2 if pending_open else 1
    )
    name_search_url = loyalty_desk_url(
        request.path, q=q if search_mode == 'name' else '', mode='name', nivo=search_nivo,
    )
    code_search_url = loyalty_desk_url(
        request.path, q=q if search_mode == 'code' else '', mode='code', nivo=search_nivo,
    )

    context = {
        **_base_context(),
        # Prazno: ne puni header polje za pretragu ARTIKALA (context_processor koristi ?q=)
        'search_query': '',
        'loyalty_search_query': q,
        'loyalty_search_mode': search_mode,
        'loyalty_search_nivo': search_nivo,
        'loyalty_name_url': name_search_url,
        'loyalty_code_url': code_search_url,
        'searched': searched,
        'cards': cards,
        'table_cards': table_cards,
        'table_page': table_page,
        'page_items': page_items,
        'desk_tab': desk_tab,
        'desk_stats': desk_stats,
        'desk_ledger': desk_ledger,
        'open_step': open_step,
        'pending_open': pending_open,
        'selected_card': selected_card,
        'user_orders': user_orders,
        'purchase_timeline': purchase_timeline,
        'loyalty': loyalty_ctx,
        'edit_form': edit_form,
        'issue_form': issue_form,
        'newly_issued': newly_issued,
        'cardholder_name': cardholder_name,
        'pending_otp': pending_otp if selected_card else None,
        'new_member': request.GET.get('novi') == '1',
        'new_member_phone': phone_local,
        'phone_local': phone_local,
        'loyalty_from_phone_fmt': loyalty_from_phone_display(),
    }
    return render(request, 'staff/loyalty_system.html', context)


@login_required(login_url='login')
@user_passes_test(_staff_required)
def staff_loyalty_member(request, kod):
    """Poseban URL za otvorenu loyalty karticu / kupca."""
    from decimal import InvalidOperation

    from .models import LoyaltyPurchase
    from .loyalty import (
        azuriraj_loyalty_karticu,
        clear_pending_purchase_otp,
        commit_loyalty_purchase,
        get_pending_purchase_otp,
        loyalty_card_share_token,
        loyalty_kontekst,
        obrisi_loyalty_kupovinu,
        online_orders_for_loyalty_card,
        osiguraj_loyalty_karticu,
        osiguraj_sestocifreni_kod,
        start_purchase_otp,
        verify_purchase_otp,
        LOYALTY_PURCHASE_OTP_SESSION_KEY,
    )

    selected_card = get_object_or_404(
        LoyaltyCard.objects.select_related('user', 'user__profil'),
        kod__iexact=kod,
    )
    selected_card = osiguraj_loyalty_karticu(selected_card.user)
    old_kod = selected_card.kod
    osiguraj_sestocifreni_kod(selected_card)
    if selected_card.kod != old_kod:
        return redirect('staff_loyalty_member', kod=selected_card.kod)
    azuriraj_loyalty_karticu(selected_card)

    def _ctx(card):
        token = loyalty_card_share_token(card)
        share_url = ''
        if token:
            path = reverse(
                'public_loyalty_card_image',
                kwargs={'card_id': card.pk, 'token': token},
            )
            share_url = request.build_absolute_uri(path)
        return loyalty_kontekst(card, share_image_url=share_url)

    member_url = request.path
    purchase_anchor = '#evidentiraj-kupovinu'
    user_orders = online_orders_for_loyalty_card(selected_card, limit=50)
    manual_purchases = list(
        LoyaltyPurchase.objects.filter(kartica=selected_card)
        .select_related('kreirao')
        .order_by('-kreirano')[:50]
    )
    purchase_timeline = []
    for order in user_orders:
        purchase_timeline.append({
            'kind': 'online',
            'date': order.kreirana,
            'amount': order.ukupno,
            'label': f'#{order.broj}',
            'status': (
                'Završena'
                if order.status in ('zavrsena', 'poslana', 'potvrdjena')
                else (order.get_status_label() if hasattr(order, 'get_status_label') else order.status)
            ),
            'status_code': order.status,
            'order': order,
            'note': '',
            'payment': 'Kartica',
            'channel': 'Web',
        })
    for pur in manual_purchases:
        status_label = (
            'Prodavnica · admin (bez koda)'
            if getattr(pur, 'verifikacija', '') == LoyaltyPurchase.Verifikacija.ADMIN
            else 'Prodavnica · kod'
        )
        purchase_timeline.append({
            'kind': 'manual',
            'date': pur.kreirano,
            'amount': pur.iznos,
            'label': 'Evidentirano',
            'status': 'Završena',
            'status_code': 'manual',
            'order': None,
            'note': pur.napomena or '',
            'purchase': pur,
            'payment': (
                'Kartica' if getattr(pur, 'placanje', '') == LoyaltyPurchase.Placanje.KARTICA
                else 'Gotovina'
            ),
            'channel': 'Prodavnica',
            'status_detail': status_label,
        })
    from django.utils import timezone as dj_tz
    purchase_timeline.sort(key=lambda row: row['date'] or dj_tz.now(), reverse=True)

    def _purchase_year(row):
        dt = row.get('date')
        if not dt:
            return dj_tz.localdate().year
        if dj_tz.is_aware(dt):
            dt = dj_tz.localtime(dt)
        return dt.year

    purchases_by_year = {}
    for row in purchase_timeline:
        purchases_by_year.setdefault(_purchase_year(row), []).append(row)
    purchase_years = sorted(purchases_by_year.keys(), reverse=True)
    try:
        purchase_year = int(request.GET.get('godina') or 0)
    except (TypeError, ValueError):
        purchase_year = 0
    if purchase_year not in purchases_by_year:
        purchase_year = purchase_years[0] if purchase_years else dj_tz.localdate().year
    year_purchases = purchases_by_year.get(purchase_year, [])
    year_total = sum((row.get('amount') or Decimal('0')) for row in year_purchases)
    profil = getattr(selected_card.user, 'profil', None)

    if request.method == 'POST' and request.POST.get('action') == 'aktiviraj_nalog':
        target = selected_card.user
        if not target.is_active:
            target.is_active = True
            target.save(update_fields=['is_active'])
            messages.success(request, f'Nalog {target.email or target.get_full_name()} je aktiviran.')
        return redirect(member_url)

    if request.method == 'POST' and request.POST.get('action') == 'deaktiviraj_karticu':
        target = selected_card.user
        if target.is_active:
            target.is_active = False
            target.save(update_fields=['is_active'])
            messages.warning(request, 'Kartica / nalog je deaktiviran.')
        return redirect(member_url)

    if request.method == 'POST' and request.POST.get('action') == 'save_note':
        note = (request.POST.get('napomena') or '').strip()[:2000]
        profil_obj, _ = UserProfile.objects.get_or_create(user=selected_card.user)
        profil_obj.loyalty_napomena = note
        profil_obj.save(update_fields=['loyalty_napomena'])
        messages.success(request, 'Napomena je sačuvana.')
        return redirect(member_url)

    if request.method == 'POST' and request.POST.get('action') == 'obrisi_kupovinu':
        try:
            iznos = obrisi_loyalty_kupovinu(selected_card, request.POST.get('purchase_id'))
            messages.success(request, f'Evidentiranje kupovine od {iznos} KM je obrisano.')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect(member_url)

    if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu':
        try:
            iznos = Decimal(request.POST.get('iznos', '0'))
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, 'Neispravan iznos.')
            return redirect(member_url)
        placanje = (request.POST.get('placanje') or 'gotovina').strip().lower()
        if placanje not in {'gotovina', 'kartica'}:
            placanje = 'gotovina'
        try:
            purchase = commit_loyalty_purchase(
                selected_card, iznos,
                napomena='',
                verifikacija=LoyaltyPurchase.Verifikacija.ADMIN,
                staff_user=request.user,
                placanje=placanje,
            )
            clear_pending_purchase_otp(request)
            nacin = 'kartično' if placanje == 'kartica' else 'gotovinski'
            messages.success(request, f'Kupovina od {purchase.iznos} KM evidentirana ({nacin}).')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect(member_url)

    if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_start':
        channel = (request.POST.get('channel') or 'whatsapp').strip().lower()
        try:
            iznos = Decimal(request.POST.get('iznos', '0'))
            napomena = (request.POST.get('napomena') or '').strip()[:200]
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, 'Neispravan iznos.')
            return redirect(member_url + purchase_anchor)
        if channel == 'admin':
            try:
                purchase = commit_loyalty_purchase(
                    selected_card, iznos,
                    napomena=napomena or 'Admin — nema internet (bez koda)',
                    verifikacija=LoyaltyPurchase.Verifikacija.ADMIN,
                    staff_user=request.user,
                )
                clear_pending_purchase_otp(request)
                messages.warning(request, f'Kupovina od {purchase.iznos} KM evidentirana BEZ koda (admin).')
            except ValueError as exc:
                messages.error(request, str(exc))
            return redirect(member_url + purchase_anchor)
        try:
            start_purchase_otp(request, selected_card, iznos, napomena)
            pending = get_pending_purchase_otp(request, card=selected_card) or {}
            pending['channel'] = channel
            request.session[LOYALTY_PURCHASE_OTP_SESSION_KEY] = pending
            request.session.modified = True
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect(member_url + purchase_anchor)

    if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_potvrdi':
        ok, result = verify_purchase_otp(request, request.POST.get('otp_code'), selected_card)
        if not ok:
            messages.error(request, result)
            return redirect(member_url + purchase_anchor)
        try:
            purchase = commit_loyalty_purchase(
                selected_card, result.get('iznos'),
                napomena=result.get('napomena') or '',
                verifikacija=LoyaltyPurchase.Verifikacija.OTP,
                staff_user=request.user,
            )
            clear_pending_purchase_otp(request)
            messages.success(request, f'Kupovina od {purchase.iznos} KM evidentirana (potvrđeno kodom).')
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect(member_url + purchase_anchor)

    if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_admin':
        try:
            pending = get_pending_purchase_otp(request, card=selected_card)
            if pending:
                iznos = Decimal(str(pending.get('iznos') or '0'))
                napomena = (pending.get('napomena') or '').strip()[:200]
            else:
                iznos = Decimal(request.POST.get('iznos', '0'))
                napomena = (request.POST.get('napomena') or '').strip()[:200]
            purchase = commit_loyalty_purchase(
                selected_card, iznos,
                napomena=napomena or 'Admin — nema internet (bez koda)',
                verifikacija=LoyaltyPurchase.Verifikacija.ADMIN,
                staff_user=request.user,
            )
            clear_pending_purchase_otp(request)
            messages.warning(request, f'Kupovina od {purchase.iznos} KM evidentirana BEZ koda (admin).')
        except (ValueError, InvalidOperation, TypeError) as exc:
            messages.error(request, str(exc) if str(exc) else 'Neispravan iznos.')
        return redirect(member_url + purchase_anchor)

    if request.method == 'POST' and request.POST.get('action') == 'evidentiraj_kupovinu_cancel':
        clear_pending_purchase_otp(request)
        return redirect(member_url + purchase_anchor)

    edit_form = None
    if request.method == 'POST' and request.POST.get('action') == 'update_profile':
        edit_form = StaffLoyaltyProfileForm(request.POST, exclude_user_id=selected_card.user_id)
        if edit_form.is_valid():
            u = selected_card.user
            ime_prezime = (edit_form.cleaned_data.get('ime_prezime') or '').strip()
            if ime_prezime:
                parts = ime_prezime.split(maxsplit=1)
                u.first_name = parts[0]
                u.last_name = parts[1] if len(parts) > 1 else ''
            new_email = (edit_form.cleaned_data.get('email') or '').strip().lower()
            u.email = new_email
            u.save(update_fields=['first_name', 'last_name', 'email'])
            profil, _ = UserProfile.objects.get_or_create(user=u)
            profil.telefon = (edit_form.cleaned_data.get('telefon') or '').strip()
            profil.adresa = (edit_form.cleaned_data.get('adresa') or '').strip()
            profil.grad = (edit_form.cleaned_data.get('grad') or '').strip()
            profil.postanski_broj = (edit_form.cleaned_data.get('postanski_broj') or '').strip()
            profil.save()
            messages.success(request, 'Podaci su ažurirani.')
            return redirect(member_url)
        messages.error(request, 'Greška pri ažuriranju (provjeri dupli email/telefon).')
    else:
        initial = {
            'ime_prezime': selected_card.user.get_full_name() or selected_card.user.first_name,
            'email': selected_card.user.email or '',
        }
        if profil:
            from .loyalty import ba_mobile_local
            initial.update({
                'telefon': ba_mobile_local(profil.telefon) or '',
                'adresa': profil.adresa or '',
                'grad': profil.grad or '',
                'postanski_broj': profil.postanski_broj or '',
            })
        edit_form = StaffLoyaltyProfileForm(initial=initial, exclude_user_id=selected_card.user_id)

    pending_otp = None
    raw_pending = get_pending_purchase_otp(request, card=selected_card)
    if raw_pending:
        from .loyalty import purchase_otp_message, sms_chat_url, viber_chat_url, whatsapp_chat_url
        tel = raw_pending.get('telefon') or ((profil.telefon if profil else '') or '')
        msg = purchase_otp_message(raw_pending.get('code') or '', iznos=raw_pending.get('iznos'))
        pending_otp = {
            'iznos': raw_pending.get('iznos'),
            'napomena': raw_pending.get('napomena') or '',
            'telefon': tel,
            'message': msg,
            'channel': raw_pending.get('channel') or '',
            'viber_url': viber_chat_url(tel, msg),
            'whatsapp_url': whatsapp_chat_url(tel, msg),
            'sms_url': sms_chat_url(tel, msg),
            'auto_open': '',
        }

    from .loyalty import (
        LOYALTY_TIERS,
        format_ba_int,
        format_ba_money,
        format_loyalty_phone,
    )

    loyalty_ctx = _ctx(selected_card)
    name = selected_card.user.get_full_name().strip() or selected_card.kod
    parts = [p for p in (selected_card.user.first_name, selected_card.user.last_name) if p]
    initials = ''.join(p[0] for p in parts)[:2].upper() or (name[:2].upper() if name else '—')
    spend = selected_card.ukupna_potrosnja or Decimal('0')
    points = int(spend)
    buy_count = len(purchase_timeline)
    avg = (spend / Decimal(buy_count)).quantize(Decimal('0.01')) if buy_count else Decimal('0')
    tier = loyalty_ctx['tier']
    next_tier = loyalty_ctx['next_tier']
    tier_en = {
        'bronza': 'BRONZE', 'srebrna': 'SILVER',
        'zlatna': 'GOLD', 'platinum': 'PLATINUM',
    }
    remain = loyalty_ctx.get('preostalo_do_sljedeceg')
    if next_tier and next_tier.get('od') is not None:
        span = Decimal(str(next_tier['od'])) - Decimal(str(tier.get('od') or 0))
        done = spend - Decimal(str(tier.get('od') or 0))
        progress_pct = 0
        if span > 0:
            progress_pct = int(max(0, min(100, (done / span) * 100)))
    else:
        progress_pct = 100
    for row in purchase_timeline:
        row['amount_fmt'] = format_ba_money(row.get('amount'))
        row['points'] = int(row.get('amount') or 0)
        row['points_fmt'] = format_ba_int(row['points'])
    paginator = Paginator(year_purchases, 10)
    try:
        purchase_page = paginator.get_page(request.GET.get('page') or 1)
    except (EmptyPage, PageNotAnInteger):
        purchase_page = paginator.get_page(1)
    member_tab = (request.GET.get('tab') or 'kupovine').strip().lower()
    if member_tab not in {'kupovine', 'bodovi', 'napomene'}:
        member_tab = 'kupovine'
    addr_bits = []
    if profil:
        if profil.adresa:
            addr_bits.append(profil.adresa)
        city = ' '.join(x for x in (profil.postanski_broj, profil.grad) if x)
        if city:
            addr_bits.append(city)
    context = {
        **_base_context(),
        'search_query': '',
        'loyalty_search_query': selected_card.kod,
        'selected_card': selected_card,
        'loyalty': loyalty_ctx,
        'user_orders': user_orders,
        'purchase_timeline': purchase_timeline,
        'purchase_page': purchase_page,
        'purchase_years': purchase_years,
        'purchase_year': purchase_year,
        'purchase_year_count': len(year_purchases),
        'purchase_year_total_fmt': format_ba_money(year_total),
        'edit_form': edit_form,
        'pending_otp': pending_otp,
        'cardholder_name': name,
        'newly_issued': request.GET.get('issued') == '1',
        'member_tab': member_tab,
        'member_initials': initials,
        'member_phone_fmt': format_loyalty_phone(loyalty_ctx.get('telefon') or ''),
        'member_since': selected_card.kreirana,
        'member_address': ', '.join(addr_bits),
        'member_note': (profil.loyalty_napomena if profil else '') or '',
        'member_tier_en': tier_en.get(selected_card.nivo, (selected_card.nivo or '').upper()),
        'member_next_en': tier_en.get((next_tier or {}).get('nivo'), '') if next_tier else '',
        'member_spend_fmt': format_ba_money(spend),
        'member_points': points,
        'member_points_fmt': format_ba_int(points),
        'member_orders_count': buy_count,
        'member_avg_fmt': format_ba_money(avg),
        'member_progress_pct': progress_pct,
        'member_remain_fmt': format_ba_int(int(remain)) if remain is not None else '',
        'member_tier_range': (
            f'{int(tier["od"])} – {int(tier["do"])} bodova'
            if tier.get('do') is not None
            else f'{int(tier["od"])}+ bodova'
        ),
        'loyalty_tiers': LOYALTY_TIERS,
    }
    return render(request, 'staff/loyalty_member.html', context)


@login_required(login_url='login')
@user_passes_test(_staff_required)
@require_GET
def staff_loyalty_card_image(request, card_id):
    """JPG zadnje strane kartice (barkod, bez QR) — za preuzimanje / ručno slanje."""
    from django.http import HttpResponse
    from .loyalty import generisi_loyalty_card_image

    card = get_object_or_404(
        LoyaltyCard.objects.select_related('user', 'user__profil'),
        pk=card_id,
    )
    name = card.user.get_full_name().strip() or (card.user.email or '').strip().lower()
    data = generisi_loyalty_card_image(card, cardholder_name=name, fmt='JPEG')
    response = HttpResponse(data, content_type='image/jpeg')
    response['Content-Disposition'] = f'attachment; filename="kartica-{card.kod}.jpg"'
    response['Cache-Control'] = 'private, max-age=60'
    return response


@require_GET
def public_loyalty_card_image(request, card_id, token):
    """Javni JPG zadnje strane (HMAC token)."""
    from django.http import Http404, HttpResponse

    from .loyalty import generisi_loyalty_card_image, verify_loyalty_card_share_token

    card = get_object_or_404(
        LoyaltyCard.objects.select_related('user', 'user__profil'),
        pk=card_id,
    )
    if not verify_loyalty_card_share_token(card, token):
        raise Http404('Kartica nije pronađena.')
    name = card.user.get_full_name().strip() or (card.user.email or '').strip().lower()
    data = generisi_loyalty_card_image(card, cardholder_name=name, fmt='JPEG')
    response = HttpResponse(data, content_type='image/jpeg')
    response['Content-Disposition'] = f'inline; filename="kartica-{card.kod}.jpg"'
    response['Cache-Control'] = 'public, max-age=300'
    response['X-Content-Type-Options'] = 'nosniff'
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
