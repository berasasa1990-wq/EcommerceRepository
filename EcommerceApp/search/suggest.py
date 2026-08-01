"""
Fast product autocomplete for /api/pretraga/.

Uses the same match + ranking as full search (apply_search_filter + order_search_queryset).
Returns only fields needed for the dropdown — no full opis / SEO / OLX payloads.
"""

from __future__ import annotations

from urllib.parse import quote

from django.db.models import Prefetch

from .normalize import normalize_search_text, sanitize_search_query
from .query import apply_search_filter
from .ranking import order_search_queryset

SEARCH_SUGGEST_LIMIT = 8
# Fetch one extra row to set has_more without loading a large pool
SEARCH_SUGGEST_FETCH = SEARCH_SUGGEST_LIMIT + 1


def _suggest_thumb_url(image_field) -> str:
    """120w thumb URL without storage.exists() (slow on cloud)."""
    if not image_field or not getattr(image_field, 'name', None):
        return ''
    name = image_field.name
    if '/' in name:
        folder, filename = name.rsplit('/', 1)
    else:
        folder, filename = '', name
    base = filename.rsplit('.', 1)[0]
    storage = image_field.storage
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


def _suggest_base_queryset(*, include_out_of_stock: bool, can_view_out_of_stock: bool):
    """
    Lean Product QS for autocomplete only.
    select_related brand/category; prefetch in-stock variations for price.
    Defer heavy text fields (opis, meta, search_document, OLX).
    """
    from EcommerceApp.models import Product, ProductVariation

    qs = Product.objects.filter(aktivan=True)
    if not include_out_of_stock and not can_view_out_of_stock:
        qs = qs.filter(na_stanju=True)

    variation_qs = ProductVariation.objects.only(
        'id',
        'artikal_id',
        'cijena',
        'akcijska_cijena',
        'akcija_postotak',
        'slika',
        'na_stanju',
        'pakovanje_komada',
    )
    if not include_out_of_stock:
        variation_qs = variation_qs.filter(na_stanju=True)

    return (
        qs.select_related('brend', 'kategorija')
        .prefetch_related(
            Prefetch('varijacije', queryset=variation_qs),
        )
        .defer(
            'opis',
            'meta_title',
            'meta_description',
            'olx_listing_url',
            'olx_listing_slug',
            'search_document',
            'naziv_normalized',
            'sifra_normalized',
            'barkod_normalized',
        )
    )


def _product_payload(product, *, price_getter=None, on_sale_getter=None) -> dict:
    """Minimal JSON fields for one product row."""
    for variation in product.varijacije.all():
        variation.artikal = product

    try:
        price = price_getter(product) if price_getter else product.katalog_prikazna_cijena
    except Exception:
        price = product.cijena
    try:
        base_price = product.katalog_bazna_cijena
    except Exception:
        base_price = product.cijena
    try:
        on_sale = on_sale_getter(product) if on_sale_getter else product.katalog_na_akciji
    except Exception:
        on_sale = False

    price_f = float(price) if price is not None else 0.0
    base_f = float(base_price) if base_price is not None else price_f
    old_price = ''
    if on_sale and base_f > price_f:
        old_price = f'{base_f:.2f}'

    image_field = product.prikazna_slika
    brand_name = ''
    if product.brend_id:
        try:
            brand_name = product.brend.naziv or ''
        except Exception:
            brand_name = ''
    cat_name = ''
    if product.kategorija_id:
        try:
            cat_name = product.kategorija.naziv or ''
        except Exception:
            cat_name = ''

    in_stock = bool(product.na_stanju)
    if not in_stock:
        try:
            in_stock = any(v.na_stanju for v in product.varijacije.all())
        except Exception:
            pass

    return {
        'type': 'product',
        'id': product.pk,
        'naziv': product.naziv or '',
        'sifra': product.sifra or '',
        'brand': brand_name,
        'category': cat_name,
        'price': f'{price_f:.2f}',
        'old_price': old_price,
        'on_sale': bool(on_sale),
        'in_stock': in_stock,
        'url': product.get_absolute_url(),
        'image': _suggest_thumb_url(image_field) if image_field else '',
    }


def _empty_payload(query: str = '') -> dict:
    return {
        'results': [],
        'query': query,
        'has_more': False,
        'show_all_url': f'/pretraga/?q={quote(query)}' if query else '/pretraga/',
        'show_all_label': (
            f'Prikaži sve rezultate za: {query}' if query else 'Prikaži sve rezultate'
        ),
    }


def build_suggest_response(
    request,
    query: str,
    *,
    can_view_out_of_stock: bool = False,
    price_getter=None,
    on_sale_getter=None,
) -> dict:
    """
    Autocomplete payload — same ranking as full search.

    Guards:
    - empty / < 2 chars → empty results (no DB match work beyond sanitize)
    - max 150 chars via sanitize_search_query
    - max 8 products, SQL ordered by search_rank + in-stock
    - only products on stock for customers (no OOS in autocomplete)
    """
    raw = sanitize_search_query(query)
    if not raw:
        return _empty_payload('')

    folded = normalize_search_text(raw)
    if len(folded) < 2 and not any(ch.isdigit() for ch in raw):
        return _empty_payload(raw)
    if len(raw) < 2 and not any(ch.isdigit() for ch in raw):
        return _empty_payload(raw)

    base_in = _suggest_base_queryset(
        include_out_of_stock=False,
        can_view_out_of_stock=can_view_out_of_stock,
    )
    # Same filter + rank pipeline; never show OOS to customers
    qs = order_search_queryset(apply_search_filter(base_in, raw), raw)
    if not can_view_out_of_stock:
        qs = qs.filter(na_stanju=True)
    pool = list(qs[:SEARCH_SUGGEST_FETCH])
    used_oos = False

    has_more = len(pool) > SEARCH_SUGGEST_LIMIT
    products = pool[:SEARCH_SUGGEST_LIMIT]

    # Deduplicate by pk (safety)
    seen: set[int] = set()
    results = []
    for product in products:
        if product.pk in seen:
            continue
        seen.add(product.pk)
        results.append(
            _product_payload(
                product,
                price_getter=price_getter,
                on_sale_getter=on_sale_getter,
            ),
        )
        if len(results) >= SEARCH_SUGGEST_LIMIT:
            break

    show_all_url = f'/pretraga/?q={quote(raw)}'
    return {
        'results': results,
        'query': raw,
        'has_more': has_more or used_oos,
        'show_all_url': show_all_url,
        'show_all_label': f'Prikaži sve rezultate za: {raw}',
        'count': len(results),
    }
