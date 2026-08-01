"""
Search relevance ranking via Django ORM (annotate + Case/When).

Relevance always dominates small business boosts.
Does not load the full product table into Python for sorting.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import (
    Case,
    Exists,
    F,
    IntegerField,
    OuterRef,
    Q,
    Value,
    When,
)

from .normalize import (
    normalize_measurements,
    normalize_search_text,
    query_variants,
    sanitize_search_query,
    tokenize_search_query,
)


@dataclass(frozen=True)
class SCORE:
    """Score bands — relevance dominates business boosts."""
    EXACT_SIFRA: int = 1000
    EXACT_BARKOD: int = 1000
    EXACT_NAME: int = 900
    NAME_STARTSWITH: int = 750
    ALL_TOKENS_IN_NAME: int = 650
    SIFRA_STARTSWITH: int = 600
    VARIATION: int = 550
    TAG: int = 450
    BRAND: int = 400
    CATEGORY: int = 350
    # Synonym match (lower than exact name/sifra/brand/category structure)
    SYNONYM: int = 300
    # Structured measure / attribute match (pre-extracted ProductAttribute)
    ATTRIBUTE: int = 280
    DESCRIPTION: int = 100
    # Fuzzy / typo similarity (always below exact name, codes, tags, brands)
    FUZZY: int = 70
    # Weak fallback if product matched only via search_document
    FALLBACK: int = 50
    # Business (small — cannot outweigh exact name/code)
    IN_STOCK: int = 40
    POPULAR: int = 20
    ON_SALE: int = 15
    NEW: int = 10
    LAGER_MAX: int = 10


def _query_terms(query: str) -> tuple[str, str, list[str], list[str]]:
    """Return (raw, folded, tokens, variants)."""
    raw = sanitize_search_query(query)
    measured = normalize_measurements(raw)
    folded = normalize_search_text(measured)
    tokens = tokenize_search_query(raw)
    variants = query_variants(raw)
    return raw, folded, tokens, variants


def _exists_variation_match(terms: list[str], folded: str):
    from EcommerceApp.models import ProductVariation

    q = Q()
    for term in terms:
        if not term:
            continue
        q |= (
            Q(sifra__iexact=term)
            | Q(naziv__iexact=term)
            | Q(sifra__istartswith=term)
            | Q(naziv__istartswith=term)
            | Q(sifra__icontains=term)
            | Q(naziv__icontains=term)
        )
    if folded:
        q |= (
            Q(sifra_normalized__iexact=folded)
            | Q(naziv_normalized__iexact=folded)
            | Q(sifra_normalized__istartswith=folded)
            | Q(naziv_normalized__istartswith=folded)
            | Q(sifra_normalized__icontains=folded)
            | Q(naziv_normalized__icontains=folded)
        )
    if not q:
        return Exists(ProductVariation.objects.none())
    return Exists(
        ProductVariation.objects.filter(artikal_id=OuterRef('pk')).filter(q),
    )


def _exists_tag_match(terms: list[str], folded: str):
    from EcommerceApp.models import Product

    through = Product.tagovi.through
    q = Q()
    for term in terms:
        if term and len(term) >= 2:
            q |= Q(tag__naziv__icontains=term)
    if not q:
        # Never match-all: empty Q() would treat every tagged product as a hit
        return Exists(through.objects.none())
    return Exists(
        through.objects.filter(product_id=OuterRef('pk')).filter(q),
    )


def _all_tokens_in_name_q(tokens: list[str]) -> Q | None:
    """AND of naziv_normalized__icontains for every token (all words in name)."""
    if len(tokens) < 2:
        return None
    q = Q()
    for i, token in enumerate(tokens):
        part = Q(naziv_normalized__icontains=token) | Q(naziv__icontains=token)
        if i == 0:
            q = part
        else:
            q &= part
    return q


def annotate_search_relevance(products_qs, query: str):
    """
    Annotate queryset with:
    - search_relevance: primary mutual-exclusive match tier (0–1000)
    - search_business: small business boosts (≤ 40+20+15+10+10)
    - search_rank: relevance + business (ORDER BY this DESC)
    - search_in_stock: 1 if product.na_stanju or any variation na_stanju

    Uses Case/When/Exists only — no Python scoring loop, no JOIN row explosion.
    """
    raw, folded, tokens, variants = _query_terms(query)
    if not raw:
        return products_qs.annotate(
            search_relevance=Value(0, output_field=IntegerField()),
            search_business=Value(0, output_field=IntegerField()),
            search_rank=Value(0, output_field=IntegerField()),
            search_in_stock=Case(
                When(na_stanju=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )

    terms = list(variants)
    if folded and folded not in {t.casefold() for t in terms}:
        terms.append(folded)

    var_exists = _exists_variation_match(terms, folded)
    tag_exists = _exists_tag_match(terms, folded)

    # —— Primary relevance (first matching When wins — priority order) ——
    whens: list[When] = []

    # 1–2 Exact sifra / barkod
    for term in terms:
        whens.append(When(sifra__iexact=term, then=Value(SCORE.EXACT_SIFRA)))
        whens.append(When(barkod__iexact=term, then=Value(SCORE.EXACT_BARKOD)))
    if folded:
        whens.append(When(sifra_normalized__iexact=folded, then=Value(SCORE.EXACT_SIFRA)))
        whens.append(When(barkod_normalized__iexact=folded, then=Value(SCORE.EXACT_BARKOD)))

    # 3 Exact name
    for term in terms:
        whens.append(When(naziv__iexact=term, then=Value(SCORE.EXACT_NAME)))
    if folded:
        whens.append(When(naziv_normalized__iexact=folded, then=Value(SCORE.EXACT_NAME)))

    # 4 Name startswith
    for term in terms:
        if len(term) >= 2:
            whens.append(When(naziv__istartswith=term, then=Value(SCORE.NAME_STARTSWITH)))
    if folded and len(folded) >= 2:
        whens.append(
            When(naziv_normalized__istartswith=folded, then=Value(SCORE.NAME_STARTSWITH)),
        )

    # 5 All query words in name
    all_tokens_q = _all_tokens_in_name_q(tokens)
    if all_tokens_q is not None:
        whens.append(When(all_tokens_q, then=Value(SCORE.ALL_TOKENS_IN_NAME)))

    # 6 Sifra startswith
    for term in terms:
        if len(term) >= 2:
            whens.append(When(sifra__istartswith=term, then=Value(SCORE.SIFRA_STARTSWITH)))
    if folded and len(folded) >= 2:
        whens.append(
            When(sifra_normalized__istartswith=folded, then=Value(SCORE.SIFRA_STARTSWITH)),
        )

    # 7 Variation name / sifra
    whens.append(When(var_exists, then=Value(SCORE.VARIATION)))

    # 8 Tag
    whens.append(When(tag_exists, then=Value(SCORE.TAG)))

    # 9 Brand
    brand_q = Q()
    for term in terms:
        if term:
            brand_q |= Q(brend__naziv__icontains=term)
    if brand_q:
        whens.append(When(brand_q, then=Value(SCORE.BRAND)))

    # 10 Category / subcategory
    cat_q = Q()
    for term in terms:
        if term:
            cat_q |= (
                Q(kategorija__naziv__icontains=term)
                | Q(kategorija__roditelj__naziv__icontains=term)
            )
    if cat_q:
        whens.append(When(cat_q, then=Value(SCORE.CATEGORY)))

    # Name contains for typed terms (above synonym-only tier)
    for term in terms:
        if term and len(term) >= 2:
            whens.append(When(naziv__icontains=term, then=Value(SCORE.ALL_TOKENS_IN_NAME - 50)))
    if folded and len(folded) >= 2:
        whens.append(
            When(naziv_normalized__icontains=folded, then=Value(SCORE.ALL_TOKENS_IN_NAME - 50)),
        )

    # Synonym-only terms: weaker than exact name/sifra (SCORE.SYNONYM = 300)
    from .synonyms import synonym_only_terms

    syn_terms = synonym_only_terms(raw)
    syn_q = Q()
    for syn in syn_terms:
        if not syn or len(syn) < 2:
            continue
        # Avoid M2M joins here (row multiplication); search_document includes tags
        syn_q |= (
            Q(naziv_normalized__icontains=syn)
            | Q(naziv__icontains=syn)
            | Q(search_document__icontains=syn)
        )
    if syn_q:
        whens.append(When(syn_q, then=Value(SCORE.SYNONYM)))

    # Structured measures (ProductAttribute) — below synonym, above description
    from .measures import attribute_exists_for_measure, parse_measures_from_text

    for measure in parse_measures_from_text(raw, context_tokens=set(tokens)):
        whens.append(
            When(attribute_exists_for_measure(measure), then=Value(SCORE.ATTRIBUTE)),
        )

    # Description (typed terms only — weaker than synonym product-name hits)
    opis_q = Q()
    for term in terms:
        if term and len(term) >= 2:
            opis_q |= Q(opis__icontains=term)
    if opis_q:
        whens.append(When(opis_q, then=Value(SCORE.DESCRIPTION)))

    relevance = Case(
        *whens,
        default=Value(SCORE.FALLBACK),
        output_field=IntegerField(),
    )

    # —— Business boosts (additive, intentionally small) ——
    from EcommerceApp.models import ProductVariation

    in_stock_exists = Exists(
        ProductVariation.objects.filter(
            artikal_id=OuterRef('pk'),
            na_stanju=True,
        ),
    )
    stock_boost = Case(
        When(na_stanju=True, then=Value(SCORE.IN_STOCK)),
        When(in_stock_exists, then=Value(SCORE.IN_STOCK)),
        default=Value(0),
        output_field=IntegerField(),
    )
    popular_boost = Case(
        When(je_hit=True, then=Value(SCORE.POPULAR)),
        default=Value(0),
        output_field=IntegerField(),
    )
    sale_boost = Case(
        When(
            akcijska_cijena__isnull=False,
            akcijska_cijena__lt=F('cijena'),
            then=Value(SCORE.ON_SALE),
        ),
        default=Value(0),
        output_field=IntegerField(),
    )
    new_boost = Case(
        When(je_novitet=True, then=Value(SCORE.NEW)),
        default=Value(0),
        output_field=IntegerField(),
    )
    lager_boost = Case(
        When(prioritet_lagera__gte=2, then=Value(SCORE.LAGER_MAX)),
        When(prioritet_lagera=1, then=Value(5)),
        default=Value(0),
        output_field=IntegerField(),
    )

    business = (
        stock_boost + popular_boost + sale_boost + new_boost + lager_boost
    )

    in_stock_flag = Case(
        When(na_stanju=True, then=Value(1)),
        When(in_stock_exists, then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )

    return products_qs.annotate(
        search_relevance=relevance,
        search_business=business,
        search_rank=relevance + business,
        search_in_stock=in_stock_flag,
    )


def order_search_queryset(
    products_qs,
    query: str,
    *,
    price_sort: str | None = None,
    sort: str | None = None,
):
    """
    Annotate relevance and ORDER BY in the database.

    Default (relevance):
    1) search_rank DESC
    2) search_in_stock DESC
    3) naziv, pk

    User sort overrides secondary keys but rank remains primary for
    najnovije/najpopularnije (rank first, then date/popularity).
    Price sorts put price first only when user explicitly chooses them.
    """
    from django.db.models import FloatField
    from django.db.models.functions import Coalesce

    from .results import (
        SORT_NEWEST,
        SORT_POPULAR,
        SORT_PRICE_ASC,
        SORT_PRICE_DESC,
        SORT_RELEVANCE,
        normalize_sort,
    )

    raw, folded, _tokens, _variants = _query_terms(query)
    qs = annotate_search_relevance(products_qs, query)

    # Optional trigram similarity bonus (Postgres only) — capped, never beats exact tiers
    rank_field = 'search_rank'
    try:
        from .fuzzy import TRGM_STRONG, TRGM_THRESHOLD, postgres_trigram_ready

        if postgres_trigram_ready() and folded and len(folded) >= 3:
            from django.contrib.postgres.search import TrigramSimilarity

            qs = qs.annotate(
                _trgm_sim=Coalesce(
                    TrigramSimilarity('naziv_normalized', folded),
                    Value(0.0),
                    output_field=FloatField(),
                ),
            )
            fuzzy_bonus = Case(
                When(_trgm_sim__gte=TRGM_STRONG, then=Value(SCORE.FUZZY)),
                When(_trgm_sim__gte=TRGM_THRESHOLD, then=Value(SCORE.FUZZY // 2)),
                default=Value(0),
                output_field=IntegerField(),
            )
            qs = qs.annotate(search_rank_with_fuzzy=F('search_rank') + fuzzy_bonus)
            rank_field = 'search_rank_with_fuzzy'
    except Exception:
        pass

    # Resolve sort mode (price_sort kept for backwards compatibility)
    sort_mode = normalize_sort(sort)
    if price_sort == 'opadajuca':
        sort_mode = SORT_PRICE_DESC
    elif price_sort == 'rastuca':
        sort_mode = SORT_PRICE_ASC

    if sort_mode == SORT_PRICE_ASC:
        order = ['cijena', f'-{rank_field}', '-search_in_stock', 'naziv', 'pk']
    elif sort_mode == SORT_PRICE_DESC:
        order = ['-cijena', f'-{rank_field}', '-search_in_stock', 'naziv', 'pk']
    elif sort_mode == SORT_NEWEST:
        # Relevance first, then newer among similar relevance
        order = [f'-{rank_field}', '-search_in_stock', '-kreiran', 'naziv', 'pk']
    elif sort_mode == SORT_POPULAR:
        order = [
            f'-{rank_field}',
            '-search_in_stock',
            '-je_hit',
            '-prioritet_lagera',
            'naziv',
            'pk',
        ]
    else:
        # SORT_RELEVANCE (default)
        order = [f'-{rank_field}', '-search_in_stock', 'naziv', 'pk']

    return qs.order_by(*order)


def apply_search_ranked(
    products_qs,
    query: str,
    *,
    price_sort: str | None = None,
    sort: str | None = None,
):
    """
    Filter matches + rank in SQL. Returns ordered QuerySet (not a list).
    Empty query: returns qs unchanged (no expensive ranking).
    """
    from .query import apply_search_filter

    raw = sanitize_search_query(query)
    if not raw:
        return products_qs
    matched = apply_search_filter(products_qs, raw)
    # distinct() before annotate if any join path; Exists-based filter is clean
    matched = matched.distinct()
    return order_search_queryset(
        matched, raw, price_sort=price_sort, sort=sort,
    )


def score_product(product, query: str, *, on_sale: bool | None = None) -> int:
    """
    Lightweight score for a single already-loaded product (tests / debugging).
    Mirrors the ORM tier logic using normalized field values when present.
    """
    raw, folded, tokens, _variants = _query_terms(query)
    if not raw:
        return 0

    name = normalize_search_text(
        normalize_measurements(getattr(product, 'naziv', '') or ''),
    )
    sifra = normalize_search_text(getattr(product, 'sifra', '') or '')
    barkod = normalize_search_text(getattr(product, 'barkod', '') or '')
    # Prefer denormalized if already populated
    if getattr(product, 'naziv_normalized', None):
        name = product.naziv_normalized or name
    if getattr(product, 'sifra_normalized', None):
        sifra = product.sifra_normalized or sifra
    if getattr(product, 'barkod_normalized', None):
        barkod = product.barkod_normalized or barkod

    opis = normalize_search_text(getattr(product, 'opis', '') or '')
    brand = ''
    if getattr(product, 'brend', None):
        brand = normalize_search_text(product.brend.naziv or '')
    cat = ''
    if getattr(product, 'kategorija', None):
        cat = normalize_search_text(product.kategorija.naziv or '')
        if product.kategorija.roditelj_id and product.kategorija.roditelj:
            cat = (
                normalize_search_text(product.kategorija.roditelj.naziv or '')
                + ' '
                + cat
            ).strip()

    # Mutual-exclusive tier (same order as Case)
    rel = SCORE.FALLBACK
    if sifra and folded and sifra == folded:
        rel = SCORE.EXACT_SIFRA
    elif barkod and folded and barkod == folded:
        rel = SCORE.EXACT_BARKOD
    elif name and folded and name == folded:
        rel = SCORE.EXACT_NAME
    elif name and folded and name.startswith(folded):
        rel = SCORE.NAME_STARTSWITH
    elif tokens and name and all(t in name for t in tokens):
        rel = SCORE.ALL_TOKENS_IN_NAME
    elif sifra and folded and sifra.startswith(folded):
        rel = SCORE.SIFRA_STARTSWITH
    else:
        var_hit = False
        try:
            for var in product.varijacije.all():
                vn = normalize_search_text(var.naziv or '')
                vs = normalize_search_text(var.sifra or '')
                if folded and (
                    vs == folded
                    or vn == folded
                    or (vs and vs.startswith(folded))
                    or (vn and (vn.startswith(folded) or folded in vn))
                ):
                    var_hit = True
                    break
        except Exception:
            pass
        if var_hit:
            rel = SCORE.VARIATION
        else:
            tag_hit = False
            try:
                for tag in product.tagovi.all():
                    tn = normalize_search_text(tag.naziv or '')
                    if folded and tn and (folded in tn or tn in folded):
                        tag_hit = True
                        break
            except Exception:
                pass
            if tag_hit:
                rel = SCORE.TAG
            elif brand and folded and folded in brand:
                rel = SCORE.BRAND
            elif cat and folded and folded in cat:
                rel = SCORE.CATEGORY
            elif opis and folded and folded in opis:
                rel = SCORE.DESCRIPTION
            elif name and folded and folded in name:
                rel = SCORE.ALL_TOKENS_IN_NAME - 50

    business = 0
    in_stock = bool(getattr(product, 'na_stanju', False))
    if not in_stock:
        try:
            in_stock = any(v.na_stanju for v in product.varijacije.all())
        except Exception:
            pass
    if in_stock:
        business += SCORE.IN_STOCK
    if getattr(product, 'je_hit', False):
        business += SCORE.POPULAR
    if on_sale is None:
        try:
            on_sale = bool(product.na_akciji)
        except Exception:
            on_sale = False
    if on_sale:
        business += SCORE.ON_SALE
    if getattr(product, 'je_novitet', False):
        business += SCORE.NEW
    try:
        prio = int(getattr(product, 'prioritet_lagera', 0) or 0)
    except (TypeError, ValueError):
        prio = 0
    if prio >= 2:
        business += SCORE.LAGER_MAX
    elif prio == 1:
        business += 5

    return rel + business


def sort_products_for_search(
    products: list,
    query: str,
    *,
    price_sort: str | None = None,
    price_getter=None,
    on_sale_getter=None,
) -> list:
    """
    Sort an already-materialized list by annotated fields if present,
    otherwise by score_product. Prefer order_search_queryset on QuerySets.
    """
    if not products:
        return products

    # If ORM already annotated, trust SQL order fields
    if hasattr(products[0], 'search_rank'):
        reverse_price = price_sort == 'opadajuca'

        def key_ann(p):
            rank = int(getattr(p, 'search_rank', 0) or 0)
            stock = int(getattr(p, 'search_in_stock', 0) or 0)
            try:
                price = float(price_getter(p) if price_getter else getattr(p, 'cijena', 0) or 0)
            except Exception:
                price = 0.0
            price_key = -price if reverse_price else price
            if price_sort not in ('opadajuca', 'rastuca'):
                price_key = 0
            return (-rank, -stock, price_key, (p.naziv or '').lower(), p.pk)

        return sorted(products, key=key_ann)

    scored = []
    for p in products:
        on_sale = None
        if on_sale_getter is not None:
            try:
                on_sale = bool(on_sale_getter(p))
            except Exception:
                on_sale = None
        sc = score_product(p, query, on_sale=on_sale)
        in_stock = bool(getattr(p, 'na_stanju', False))
        if not in_stock:
            try:
                in_stock = any(v.na_stanju for v in p.varijacije.all())
            except Exception:
                pass
        try:
            price = float(price_getter(p) if price_getter else 0) or 0.0
        except Exception:
            price = 0.0
        scored.append((sc, in_stock, price, (p.naziv or '').lower(), p.pk, p))

    reverse_price = price_sort == 'opadajuca'

    def key(item):
        sc, in_stock, price, name, pk, _p = item
        price_key = -price if reverse_price else price
        if price_sort not in ('opadajuca', 'rastuca'):
            price_key = 0
        return (-sc, 0 if in_stock else 1, price_key, name, pk)

    scored.sort(key=key)
    return [item[-1] for item in scored]
