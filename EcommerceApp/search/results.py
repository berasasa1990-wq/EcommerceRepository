"""
Helpers for the full search-results page (/pretraga/).
Related brands/categories, active filter chips, sort keys.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlencode

from django.urls import reverse

from .normalize import sanitize_search_query

# Canonical GET param names (also accept aliases in views)
SORT_RELEVANCE = 'relevance'
SORT_PRICE_ASC = 'rastuca'
SORT_PRICE_DESC = 'opadajuca'
SORT_NEWEST = 'najnovije'
SORT_POPULAR = 'najpopularnije'

SORT_CHOICES = (
    (SORT_RELEVANCE, 'Najrelevantnije'),
    (SORT_PRICE_ASC, 'Cijena rastuće'),
    (SORT_PRICE_DESC, 'Cijena opadajuće'),
    (SORT_NEWEST, 'Najnovije'),
    (SORT_POPULAR, 'Najpopularnije'),
)


def normalize_sort(sort: str | None) -> str:
    s = (sort or '').strip().lower()
    aliases = {
        '': SORT_RELEVANCE,
        'relevance': SORT_RELEVANCE,
        'najrelevantnije': SORT_RELEVANCE,
        'relevantnost': SORT_RELEVANCE,
        'price_asc': SORT_PRICE_ASC,
        'rastuca': SORT_PRICE_ASC,
        'price_desc': SORT_PRICE_DESC,
        'opadajuca': SORT_PRICE_DESC,
        'newest': SORT_NEWEST,
        'najnovije': SORT_NEWEST,
        'popular': SORT_POPULAR,
        'najpopularnije': SORT_POPULAR,
    }
    return aliases.get(s, SORT_RELEVANCE if not s else s)


def related_brands_from_products(products, *, limit: int = 8) -> list[dict]:
    """Top brands among search hits (for chips / sidebar)."""
    counts: Counter = Counter()
    brand_meta: dict[int, object] = {}
    for p in products:
        if not p.brend_id:
            continue
        counts[p.brend_id] += 1
        brand_meta[p.brend_id] = p.brend
    out = []
    for bid, count in counts.most_common(limit):
        b = brand_meta.get(bid)
        if not b:
            continue
        out.append({
            'id': bid,
            'naziv': b.naziv,
            'slug': b.slug,
            'count': count,
            'url_param': b.slug,
        })
    return out


def related_categories_from_products(products, *, limit: int = 8) -> list[dict]:
    """Top categories among search hits."""
    counts: Counter = Counter()
    cat_meta: dict[int, object] = {}
    for p in products:
        if not p.kategorija_id:
            continue
        counts[p.kategorija_id] += 1
        cat_meta[p.kategorija_id] = p.kategorija
    out = []
    for cid, count in counts.most_common(limit):
        c = cat_meta.get(cid)
        if not c:
            continue
        label = c.naziv
        if getattr(c, 'roditelj_id', None) and getattr(c, 'roditelj', None):
            label = f'{c.roditelj.naziv} → {c.naziv}'
        out.append({
            'id': cid,
            'naziv': label,
            'slug': c.slug,
            'count': count,
            'url_param': c.slug,
            'is_sub': bool(getattr(c, 'roditelj_id', None)),
        })
    return out


def build_active_filters(params: dict, *, brands=None, categories=None, tags=None) -> list[dict]:
    """
    Human-readable active filter chips with remove URLs (relative query without that key).
    """
    chips = []
    q = sanitize_search_query(params.get('q') or '')

    def chip(key, label, value_label, clear_params):
        chips.append({
            'key': key,
            'label': label,
            'value': value_label,
            'clear_query': urlencode({k: v for k, v in clear_params.items() if v}),
        })

    base = {k: v for k, v in params.items() if v}

    if params.get('kategorija'):
        name = params['kategorija']
        if categories:
            for c in categories:
                if c.slug == params['kategorija']:
                    name = str(c)
                    break
        cleared = dict(base)
        cleared.pop('kategorija', None)
        cleared.pop('potkategorija', None)
        chip('kategorija', 'Kategorija', name, cleared)

    if params.get('potkategorija'):
        name = params['potkategorija']
        if categories:
            for c in categories:
                if c.slug == params['potkategorija']:
                    name = c.naziv
                    break
        cleared = dict(base)
        cleared.pop('potkategorija', None)
        chip('potkategorija', 'Potkategorija', name, cleared)

    if params.get('brend'):
        name = params['brend']
        if brands:
            for b in brands:
                if b.slug == params['brend']:
                    name = b.naziv
                    break
        cleared = dict(base)
        cleared.pop('brend', None)
        cleared.pop('brand', None)
        chip('brend', 'Brend', name, cleared)

    if params.get('cijena_od'):
        cleared = dict(base)
        cleared.pop('cijena_od', None)
        chip('cijena_od', 'Cijena od', f"{params['cijena_od']} KM", cleared)

    if params.get('cijena_do'):
        cleared = dict(base)
        cleared.pop('cijena_do', None)
        chip('cijena_do', 'Cijena do', f"{params['cijena_do']} KM", cleared)

    if params.get('na_stanju') in ('1', 'true', 'da', 'yes'):
        cleared = dict(base)
        cleared.pop('na_stanju', None)
        cleared.pop('in_stock', None)
        chip('na_stanju', 'Stanje', 'Samo na stanju', cleared)

    if params.get('akcija'):
        cleared = dict(base)
        cleared.pop('akcija', None)
        chip('akcija', 'Akcija', 'Samo akcija', cleared)

    if params.get('noviteti'):
        cleared = dict(base)
        cleared.pop('noviteti', None)
        chip('noviteti', 'Novitet', 'Samo noviteti', cleared)

    if params.get('tehnika'):
        name = params['tehnika']
        if tags:
            for t in tags:
                if t.slug == params['tehnika'] or t.naziv.lower() == name.lower():
                    name = t.naziv
                    break
        cleared = dict(base)
        cleared.pop('tehnika', None)
        chip('tehnika', 'Tehnika', name, cleared)

    if params.get('vrsta_ribe'):
        name = params['vrsta_ribe']
        if tags:
            for t in tags:
                if t.slug == params['vrsta_ribe'] or t.naziv.lower() == name.lower():
                    name = t.naziv
                    break
        cleared = dict(base)
        cleared.pop('vrsta_ribe', None)
        chip('vrsta_ribe', 'Vrsta ribe', name, cleared)

    if params.get('velicina'):
        cleared = dict(base)
        cleared.pop('velicina', None)
        chip('velicina', 'Veličina', params['velicina'], cleared)

    return chips


def search_page_query_string(params: dict, *, page: int | None = None, **overrides) -> str:
    data = {k: v for k, v in params.items() if v}
    for k, v in overrides.items():
        if v:
            data[k] = v
        else:
            data.pop(k, None)
    if page and page > 1:
        data['page'] = str(page)
    else:
        data.pop('page', None)
    return urlencode(data)
