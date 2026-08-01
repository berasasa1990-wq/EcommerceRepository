"""
Fuzzy / typo-tolerant search helpers.

PostgreSQL: pg_trgm via TrigramSimilarity / TrigramWordSimilarity.
SQLite: safe difflib fallback (weaker, never raises).

Fuzzy is only applied when strict search returns too few results.
Exact / startswith / contains ranking always outranks fuzzy bonuses.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from urllib.parse import quote

from django.db import connection
from django.db.models import Q, QuerySet

from .normalize import normalize_search_text, sanitize_search_query, tokenize_search_query

# Safe similarity floors (avoid irrelevant noise)
TRGM_THRESHOLD = 0.42
TRGM_STRONG = 0.55
SQLITE_RATIO_THRESHOLD = 0.58
DID_YOU_MEAN_THRESHOLD = 0.62
MIN_FUZZY_LEN = 4
MIN_STRONG_RESULTS = 3  # below this, expand with fuzzy candidates
FUZZY_CANDIDATE_LIMIT = 48
DID_YOU_MEAN_POOL = 80


def is_postgres() -> bool:
    try:
        return connection.vendor == 'postgresql'
    except Exception:
        return False


def postgres_trigram_ready() -> bool:
    """True when on PostgreSQL and Django postgres search can be imported."""
    if not is_postgres():
        return False
    try:
        from django.contrib.postgres.search import TrigramSimilarity  # noqa: F401
        return True
    except Exception:
        return False


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _best_token_ratio(query_fold: str, target_fold: str) -> float:
    """Max similarity of query vs whole target and vs each word of target."""
    if not query_fold or not target_fold:
        return 0.0
    best = _ratio(query_fold, target_fold)
    for word in target_fold.split():
        if len(word) < 3:
            continue
        best = max(best, _ratio(query_fold, word))
        # also partial: query vs word start
        if word.startswith(query_fold[:3]) or query_fold.startswith(word[:3]):
            best = max(best, _ratio(query_fold, word))
    return best


def _fuzzy_query_key(query: str) -> str:
    raw = sanitize_search_query(query)
    return normalize_search_text(raw)


def should_use_fuzzy(strict_count: int, query: str) -> bool:
    """Activate fuzzy only when strict hits are scarce and query is long enough."""
    key = _fuzzy_query_key(query)
    if len(key) < MIN_FUZZY_LEN and not any(ch.isdigit() for ch in key):
        # Allow 3-letter typos only if no strict results
        if len(key) < 3:
            return False
        return strict_count == 0
    return strict_count < MIN_STRONG_RESULTS


# ---------------------------------------------------------------------------
# PostgreSQL path
# ---------------------------------------------------------------------------

def _postgres_fuzzy_product_ids(query: str, *, limit: int = FUZZY_CANDIDATE_LIMIT) -> list[int]:
    from django.contrib.postgres.search import TrigramSimilarity, TrigramWordSimilarity
    from django.db.models import FloatField, Value
    from django.db.models.functions import Coalesce, Greatest

    from EcommerceApp.models import Brand, Category, Product, Tag

    folded = _fuzzy_query_key(query)
    if len(folded) < 3:
        return []

    # Products by name (normalized preferred)
    product_ids: list[int] = []
    try:
        qs = (
            Product.objects.filter(aktivan=True, na_stanju=True)
            .annotate(
                sim_name=Coalesce(
                    TrigramSimilarity('naziv_normalized', folded),
                    Value(0.0),
                    output_field=FloatField(),
                ),
                sim_word=Coalesce(
                    TrigramWordSimilarity(folded, 'naziv_normalized'),
                    Value(0.0),
                    output_field=FloatField(),
                ),
                sim_raw=Coalesce(
                    TrigramSimilarity('naziv', folded),
                    Value(0.0),
                    output_field=FloatField(),
                ),
            )
            .annotate(sim=Greatest('sim_name', 'sim_word', 'sim_raw'))
            .filter(sim__gte=TRGM_THRESHOLD)
            .order_by('-sim')
            .values_list('id', flat=True)[:limit]
        )
        product_ids.extend(list(qs))
    except Exception:
        return []

    # Brands / categories / tags → related products
    try:
        brand_ids = list(
            Brand.objects.annotate(
                sim=Coalesce(TrigramSimilarity('naziv', folded), Value(0.0)),
            )
            .filter(sim__gte=TRGM_THRESHOLD)
            .order_by('-sim')
            .values_list('id', flat=True)[:12]
        )
        if brand_ids:
            product_ids.extend(
                Product.objects.filter(
                    aktivan=True, na_stanju=True, brend_id__in=brand_ids,
                )
                .values_list('id', flat=True)[:limit]
            )
    except Exception:
        pass

    try:
        cat_ids = list(
            Category.objects.filter(aktivan=True)
            .annotate(sim=Coalesce(TrigramSimilarity('naziv', folded), Value(0.0)))
            .filter(sim__gte=TRGM_THRESHOLD)
            .order_by('-sim')
            .values_list('id', flat=True)[:12]
        )
        if cat_ids:
            product_ids.extend(
                Product.objects.filter(
                    aktivan=True, na_stanju=True, kategorija_id__in=cat_ids,
                )
                .values_list('id', flat=True)[:limit]
            )
    except Exception:
        pass

    try:
        tag_ids = list(
            Tag.objects.annotate(
                sim=Coalesce(TrigramSimilarity('naziv', folded), Value(0.0)),
            )
            .filter(sim__gte=TRGM_THRESHOLD)
            .order_by('-sim')
            .values_list('id', flat=True)[:12]
        )
        if tag_ids:
            product_ids.extend(
                Product.objects.filter(
                    aktivan=True, na_stanju=True, tagovi__id__in=tag_ids,
                )
                .values_list('id', flat=True)
                .distinct()[:limit]
            )
    except Exception:
        pass

    # Synonyms (in-memory map, already cached)
    try:
        from .synonyms import get_synonym_map, expand_term

        syn_map = get_synonym_map()
        best_syn = None
        best_score = 0.0
        for term in syn_map.keys():
            sc = _ratio(folded, term)
            if sc > best_score:
                best_score = sc
                best_syn = term
        if best_syn and best_score >= TRGM_THRESHOLD:
            for alt in expand_term(best_syn)[:8]:
                product_ids.extend(
                    Product.objects.filter(aktivan=True, na_stanju=True)
                    .filter(
                        Q(naziv_normalized__icontains=alt)
                        | Q(search_document__icontains=alt),
                    )
                    .values_list('id', flat=True)[:20]
                )
    except Exception:
        pass

    # Dedupe preserve order
    seen = set()
    out = []
    for pk in product_ids:
        if pk not in seen:
            seen.add(pk)
            out.append(pk)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# SQLite safe fallback
# ---------------------------------------------------------------------------

def _sqlite_fuzzy_product_ids(query: str, *, limit: int = FUZZY_CANDIDATE_LIMIT) -> list[int]:
    """
    Safe fallback: limited candidate pool + SequenceMatcher.
    Never raises; returns [] on any failure.
    """
    try:
        from EcommerceApp.models import Brand, Category, Product, Tag
        from .synonyms import expand_term, get_synonym_map
    except Exception:
        return []

    folded = _fuzzy_query_key(query)
    if len(folded) < 3:
        return []

    prefix = folded[:3]
    scored: list[tuple[float, int]] = []

    try:
        candidates = (
            Product.objects.filter(aktivan=True, na_stanju=True)
            .filter(
                Q(naziv_normalized__icontains=prefix)
                | Q(naziv__icontains=prefix)
                | Q(brend__naziv__icontains=prefix),
            )
            .select_related('brend')
            .only('id', 'naziv', 'naziv_normalized', 'brend__naziv')[:220]
        )
        for p in candidates:
            name = p.naziv_normalized or normalize_search_text(p.naziv or '')
            sim = _best_token_ratio(folded, name)
            if p.brend_id and p.brend:
                sim = max(sim, _best_token_ratio(folded, normalize_search_text(p.brend.naziv or '')))
            if sim >= SQLITE_RATIO_THRESHOLD:
                scored.append((sim, p.pk))
    except Exception:
        pass

    try:
        for brand in Brand.objects.only('id', 'naziv')[:300]:
            sim = _best_token_ratio(folded, normalize_search_text(brand.naziv or ''))
            if sim >= SQLITE_RATIO_THRESHOLD:
                for pk in Product.objects.filter(
                    aktivan=True, na_stanju=True, brend_id=brand.pk,
                ).values_list('id', flat=True)[:15]:
                    scored.append((sim, pk))
    except Exception:
        pass

    try:
        for cat in Category.objects.filter(aktivan=True).only('id', 'naziv')[:300]:
            sim = _best_token_ratio(folded, normalize_search_text(cat.naziv or ''))
            if sim >= SQLITE_RATIO_THRESHOLD:
                for pk in Product.objects.filter(
                    aktivan=True, na_stanju=True, kategorija_id=cat.pk,
                ).values_list('id', flat=True)[:15]:
                    scored.append((sim, pk))
    except Exception:
        pass

    try:
        for tag in Tag.objects.only('id', 'naziv')[:400]:
            sim = _best_token_ratio(folded, normalize_search_text(tag.naziv or ''))
            if sim >= SQLITE_RATIO_THRESHOLD:
                for pk in Product.objects.filter(
                    aktivan=True, na_stanju=True, tagovi__id=tag.pk,
                ).values_list('id', flat=True).distinct()[:15]:
                    scored.append((sim, pk))
    except Exception:
        pass

    try:
        syn_map = get_synonym_map()
        for term, members in syn_map.items():
            sim = _best_token_ratio(folded, term)
            if sim < SQLITE_RATIO_THRESHOLD:
                continue
            for alt in members[:6]:
                for pk in Product.objects.filter(aktivan=True, na_stanju=True).filter(
                    Q(naziv_normalized__icontains=alt) | Q(search_document__icontains=alt),
                ).values_list('id', flat=True)[:12]:
                    scored.append((sim, pk))
    except Exception:
        pass

    scored.sort(key=lambda x: (-x[0], x[1]))
    seen = set()
    out = []
    for _sim, pk in scored:
        if pk in seen:
            continue
        seen.add(pk)
        out.append(pk)
        if len(out) >= limit:
            break
    return out


def fuzzy_product_ids(query: str, *, limit: int = FUZZY_CANDIDATE_LIMIT) -> list[int]:
    """Backend-agnostic fuzzy product id list."""
    if postgres_trigram_ready():
        try:
            return _postgres_fuzzy_product_ids(query, limit=limit)
        except Exception:
            return _sqlite_fuzzy_product_ids(query, limit=limit)
    return _sqlite_fuzzy_product_ids(query, limit=limit)


def expand_queryset_with_fuzzy(
    base_qs: QuerySet,
    strict_qs: QuerySet,
    query: str,
) -> tuple[QuerySet, bool]:
    """
    Union strict results with fuzzy candidates when strict set is too small.

    Returns (queryset, fuzzy_used).
    """
    raw = sanitize_search_query(query)
    if not raw:
        return strict_qs, False

    try:
        strict_count = strict_qs.count()
    except Exception:
        return strict_qs, False

    if not should_use_fuzzy(strict_count, raw):
        return strict_qs, False

    ids = fuzzy_product_ids(raw)
    if not ids:
        return strict_qs, False

    try:
        fuzzy_qs = base_qs.filter(pk__in=ids)
        if strict_count == 0:
            return fuzzy_qs.distinct(), True
        return (strict_qs | fuzzy_qs).distinct(), True
    except Exception:
        return strict_qs, False


# ---------------------------------------------------------------------------
# Da li ste mislili
# ---------------------------------------------------------------------------

def suggest_did_you_mean(
    query: str,
    *,
    result_count: int | None = None,
) -> dict | None:
    """
    Suggest a corrected search phrase without changing the user's query.

    Returns:
        {'suggestion': 'Shimano', 'url': '/?q=Shimano', 'score': 0.77}
        or None.
    """
    raw = sanitize_search_query(query)
    folded = normalize_search_text(raw)
    if len(folded) < 3:
        return None

    # Skip if already plenty of results (unless zero)
    if result_count is not None and result_count >= MIN_STRONG_RESULTS:
        return None

    candidates: list[tuple[float, str]] = []

    def consider(display: str, score: float) -> None:
        disp = (display or '').strip()
        if not disp:
            return
        disp_fold = normalize_search_text(disp)
        if not disp_fold or disp_fold == folded:
            return
        if score < DID_YOU_MEAN_THRESHOLD:
            return
        candidates.append((score, disp))

    # Brands
    try:
        from EcommerceApp.models import Brand, Category, Product, Tag

        if postgres_trigram_ready():
            from django.contrib.postgres.search import TrigramSimilarity
            from django.db.models import Value
            from django.db.models.functions import Coalesce

            for row in (
                Brand.objects.annotate(
                    sim=Coalesce(TrigramSimilarity('naziv', folded), Value(0.0)),
                )
                .filter(sim__gte=DID_YOU_MEAN_THRESHOLD)
                .order_by('-sim')
                .values_list('naziv', 'sim')[:15]
            ):
                consider(row[0], float(row[1]))
            for row in (
                Category.objects.filter(aktivan=True)
                .annotate(sim=Coalesce(TrigramSimilarity('naziv', folded), Value(0.0)))
                .filter(sim__gte=DID_YOU_MEAN_THRESHOLD)
                .order_by('-sim')
                .values_list('naziv', 'sim')[:15]
            ):
                consider(row[0], float(row[1]))
            for row in (
                Tag.objects.annotate(
                    sim=Coalesce(TrigramSimilarity('naziv', folded), Value(0.0)),
                )
                .filter(sim__gte=DID_YOU_MEAN_THRESHOLD)
                .order_by('-sim')
                .values_list('naziv', 'sim')[:15]
            ):
                consider(row[0], float(row[1]))
            for row in (
                Product.objects.filter(aktivan=True, na_stanju=True)
                .exclude(naziv_normalized='')
                .annotate(
                    sim=Coalesce(TrigramSimilarity('naziv_normalized', folded), Value(0.0)),
                )
                .filter(sim__gte=DID_YOU_MEAN_THRESHOLD)
                .order_by('-sim')
                .values_list('naziv', 'sim')[:20]
            ):
                # Prefer short brand-like tokens from product name (first word)
                naziv, sim = row[0], float(row[1])
                consider(naziv, sim)
                first = (naziv or '').split()[0] if naziv else ''
                if first:
                    consider(first, _ratio(folded, normalize_search_text(first)))
        else:
            for brand in Brand.objects.only('naziv')[:300]:
                consider(brand.naziv, _best_token_ratio(folded, normalize_search_text(brand.naziv or '')))
            for cat in Category.objects.filter(aktivan=True).only('naziv')[:300]:
                consider(cat.naziv, _best_token_ratio(folded, normalize_search_text(cat.naziv or '')))
            for tag in Tag.objects.only('naziv')[:400]:
                consider(tag.naziv, _best_token_ratio(folded, normalize_search_text(tag.naziv or '')))
            for p in (
                Product.objects.filter(aktivan=True, na_stanju=True)
                .exclude(naziv_normalized='')
                .only('naziv', 'naziv_normalized')[:DID_YOU_MEAN_POOL]
            ):
                name_f = p.naziv_normalized or normalize_search_text(p.naziv or '')
                sc = _best_token_ratio(folded, name_f)
                if sc >= DID_YOU_MEAN_THRESHOLD:
                    # Suggest the best matching word from product name
                    words = (p.naziv or '').split()
                    best_w, best_s = p.naziv, sc
                    for w in words:
                        ws = _ratio(folded, normalize_search_text(w))
                        if ws > best_s:
                            best_w, best_s = w, ws
                    consider(best_w, best_s)
    except Exception:
        pass

    # Synonyms
    try:
        from .synonyms import get_synonym_map

        for term, members in get_synonym_map().items():
            sc = _ratio(folded, term)
            if sc >= DID_YOU_MEAN_THRESHOLD:
                # Prefer "canonical" member (first) as display — use first member title case
                display = members[0] if members else term
                # Try to find nicer casing from members list (original pojmovi are normalized)
                consider(display, sc)
                for m in members:
                    consider(m, _ratio(folded, m))
    except Exception:
        pass

    if not candidates:
        return None

    candidates.sort(key=lambda x: (-x[0], len(x[1])))
    suggestion = candidates[0][1]
    score = candidates[0][0]
    # Capitalize lightly for display if all lower
    if suggestion == suggestion.lower() and suggestion.isalpha():
        suggestion = suggestion[:1].upper() + suggestion[1:]

    return {
        'suggestion': suggestion,
        'url': f'/pretraga/?q={quote(suggestion)}',
        'score': round(score, 3),
        'label': f'Da li ste mislili: {suggestion}?',
    }
