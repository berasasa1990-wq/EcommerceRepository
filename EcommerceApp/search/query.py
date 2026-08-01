"""
Basic multi-field product search via Django ORM.

No Elasticsearch / Algolia / Meilisearch / Typesense.
No Python loop over all products.

Uses denormalized fields (naziv_normalized, search_document, …) so
"stap" finds "štap" on both SQLite and PostgreSQL.
Synonym groups expand tokens via cached map (search.synonyms).
"""

from __future__ import annotations

from django.db.models import Exists, OuterRef, Prefetch, Q

from .measures import (
    attribute_exists_for_measure,
    parse_measures_from_text,
    strip_measures_from_text,
)
from .normalize import (
    normalize_search_text,
    query_variants,
    sanitize_search_query,
    tokenize_search_query,
)
from .synonyms import expand_tokens_for_and_match, expand_query_terms


def _match_q_for_term(term: str) -> Q:
    """
    Parameterized ORM match for one term across existing product fields.

    Covered fields (when present on models):
    - Product.naziv (+ naziv_normalized)
    - Product.sifra (+ sifra_normalized)
    - Product.barkod (+ barkod_normalized)
    - Product.opis
    - Brand.naziv
    - Category.naziv + parent (potkategorija)
    - Tag.naziv (M2M)
    - ProductVariation.naziv / sifra (+ normalized)
    - search_document (blob: naziv, codes, brand, category, tags, variations, opis)
    """
    from EcommerceApp.models import Product, ProductVariation

    if not term:
        return Q(pk__in=[])

    folded = normalize_search_text(term) or term.casefold()

    through = Product.tagovi.through
    variation_match = ProductVariation.objects.filter(artikal_id=OuterRef('pk')).filter(
        Q(sifra__icontains=term)
        | Q(naziv__icontains=term)
        | Q(sifra_normalized__icontains=folded)
        | Q(naziv_normalized__icontains=folded),
    )
    tag_match = through.objects.filter(
        product_id=OuterRef('pk'),
        tag__naziv__icontains=term,
    )

    return (
        Q(naziv__icontains=term)
        | Q(sifra__icontains=term)
        | Q(barkod__icontains=term)
        | Q(opis__icontains=term)
        | Q(brend__naziv__icontains=term)
        | Q(kategorija__naziv__icontains=term)
        | Q(kategorija__roditelj__naziv__icontains=term)
        | Q(naziv_normalized__icontains=folded)
        | Q(sifra_normalized__icontains=folded)
        | Q(barkod_normalized__icontains=folded)
        | Q(search_document__icontains=folded)
        | Exists(variation_match)
        | Exists(tag_match)
    )


def apply_search_filter(products_qs, query: str):
    """
    Apply multi-field search filter to a Product queryset.

    - Empty / whitespace-only query: returns queryset unchanged
      (caller must not treat empty q as an active search).
    - Queries shorter than 2 characters (and non-numeric): empty result.
    - Max length enforced in sanitize_search_query (150).
    - Measures (3.60m, 150g, …) match ProductAttribute (pre-extracted), not live opis parse.
    - All matching via Django ORM (parameterized), never raw SQL with user input.
    """
    raw = sanitize_search_query(query)
    if not raw:
        return products_qs

    # Context tokens for measure type inference (folded)
    folded_full = normalize_search_text(raw)
    context_tokens = set(tokenize_search_query(raw))
    measures = parse_measures_from_text(raw, context_tokens=context_tokens)

    # Text part of query without measure tokens (e.g. "feeder stap 3.60 150g" → "feeder stap")
    text_raw = strip_measures_from_text(raw)
    text_raw = sanitize_search_query(text_raw)

    has_text = bool(text_raw) and (
        len(normalize_search_text(text_raw)) >= 2
        or any(ch.isdigit() for ch in text_raw)
    )
    has_measures = bool(measures)

    if not has_text and not has_measures:
        folded_len = len(folded_full)
        if folded_len < 2 and not any(ch.isdigit() for ch in raw):
            return products_qs.none()
        if len(raw) < 2 and not any(ch.isdigit() for ch in raw):
            return products_qs.none()

    match = Q()

    if has_text:
        variants = query_variants(text_raw)
        for term in variants:
            match |= _match_q_for_term(term)
        for term in expand_query_terms(text_raw):
            match |= _match_q_for_term(term)

        token_groups = expand_tokens_for_and_match(text_raw)
        if len(token_groups) >= 2:
            token_and = Q()
            for i, alts in enumerate(token_groups):
                tq = Q()
                for alt in alts:
                    tq |= _match_q_for_term(alt)
                if i == 0:
                    token_and = tq
                else:
                    token_and &= tq
            match = match | token_and
    elif not has_measures:
        # fallback: original full-string match
        variants = query_variants(raw)
        if not variants:
            return products_qs.none()
        for term in variants:
            match |= _match_q_for_term(term)
        for term in expand_query_terms(raw):
            match |= _match_q_for_term(term)

    qs = products_qs
    if has_text or (not has_measures):
        qs = qs.filter(match)

    # Each measure: structured ProductAttribute OR textual fallback (name/sifra/document)
    # so search works before extract_product_attributes is run.
    for measure in measures:
        text_fallback = Q()
        for term in (
            measure.source_text,
            f'{measure.numeric_value}{measure.unit}',
            f'{measure.normalized_value}{measure.canonical_unit}',
            str(measure.numeric_value).rstrip('0').rstrip('.') + (measure.unit or ''),
        ):
            t = (term or '').strip()
            if t:
                text_fallback |= _match_q_for_term(t)
        # Normalized forms in search_document / naziv_normalized (e.g. 3.6m)
        if measure.canonical_unit:
            nv = f'{measure.normalized_value:f}'.rstrip('0').rstrip('.')
            text_fallback |= _match_q_for_term(f'{nv}{measure.canonical_unit}')
            text_fallback |= Q(search_document__icontains=f'{nv}{measure.canonical_unit}')
            text_fallback |= Q(naziv_normalized__icontains=f'{nv}{measure.canonical_unit}')
        qs = qs.filter(attribute_exists_for_measure(measure) | text_fallback)

    if not has_text and not has_measures:
        return products_qs.none()

    strict_qs = qs.distinct()

    # Fuzzy (typos) only when strict hits are scarce — never replaces exact ranking
    try:
        from .fuzzy import expand_queryset_with_fuzzy

        expanded, _fuzzy_used = expand_queryset_with_fuzzy(
            products_qs, strict_qs, text_raw or raw,
        )
        return expanded
    except Exception:
        return strict_qs


def search_products_ranked(products_qs, query: str, *, price_sort: str | None = None):
    """
    Filter + SQL rank/order. Prefer this over list()+Python sort for search.
    """
    from .ranking import apply_search_ranked

    return apply_search_ranked(products_qs, query, price_sort=price_sort)


def search_product_queryset(
    request=None,
    *,
    include_out_of_stock: bool = False,
    can_view_out_of_stock: bool = False,
):
    """
    Base Product queryset for search / autocomplete.
    Uses select_related / prefetch_related — no N+1 on cards.
    """
    from EcommerceApp.models import Product, ProductVariation, Tag

    qs = Product.objects.filter(aktivan=True)
    if not include_out_of_stock and not can_view_out_of_stock:
        qs = qs.filter(na_stanju=True)

    return qs.select_related(
        'brend',
        'kategorija',
        'kategorija__roditelj',
    ).prefetch_related(
        Prefetch(
            'varijacije',
            queryset=ProductVariation.objects.only(
                'id',
                'artikal_id',
                'naziv',
                'sifra',
                'cijena',
                'akcijska_cijena',
                'akcija_postotak',
                'slika',
                'na_stanju',
                'stanje',
                'pakovanje_komada',
            ),
        ),
        Prefetch('tagovi', queryset=Tag.objects.only('id', 'naziv')),
    )
