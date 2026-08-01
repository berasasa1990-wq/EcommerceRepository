"""
Search synonym service — cached map of normalized term → related terms.

Loaded once per process from cache/DB; invalidated on admin save/delete.
Does not query the synonym tables on every product match.
"""

from __future__ import annotations

from django.core.cache import cache

from .normalize import normalize_search_text, sanitize_search_query, tokenize_search_query

CACHE_KEY = 'ecommerce_search_synonym_map_v1'
CACHE_TTL_SECONDS = 60 * 60 * 6  # 6h safety TTL; primary invalidation is signal/save

# Max synonym expansions per token (guard against huge groups)
MAX_SYNONYMS_PER_TERM = 24


def invalidate_synonym_cache() -> None:
    """Clear cached synonym map (call after group/synonym create/update/delete)."""
    cache.delete(CACHE_KEY)


def _load_synonym_map_from_db() -> dict[str, list[str]]:
    """
    Build map: normalized_term -> sorted unique list of all terms in its group(s).

    Only active groups. Higher group prioritet wins when a term appears in
    multiple groups (terms from higher-priority group first, then merged).
    """
    from EcommerceApp.models import SearchSynonym, SearchSynonymGroup

    # term -> list of group ids ordered by priority
    groups = list(
        SearchSynonymGroup.objects.filter(aktivno=True)
        .order_by('-prioritet', 'id')
        .prefetch_related('sinonimi'),
    )
    # Collect members per group
    group_members: dict[int, list[str]] = {}
    for g in groups:
        members = []
        seen = set()
        for s in g.sinonimi.all():
            norm = (s.normalizovani_pojam or normalize_search_text(s.pojam or '')).strip()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            members.append(norm)
        if members:
            group_members[g.pk] = members

    # Map each term to union of all groups it belongs to (priority order)
    term_to_groups: dict[str, list[int]] = {}
    for g in groups:
        members = group_members.get(g.pk) or []
        for m in members:
            term_to_groups.setdefault(m, []).append(g.pk)

    result: dict[str, list[str]] = {}
    for term, gids in term_to_groups.items():
        combined: list[str] = []
        seen = set()
        for gid in gids:
            for m in group_members.get(gid, []):
                if m not in seen:
                    seen.add(m)
                    combined.append(m)
                if len(combined) >= MAX_SYNONYMS_PER_TERM:
                    break
            if len(combined) >= MAX_SYNONYMS_PER_TERM:
                break
        result[term] = combined
    return result


def get_synonym_map() -> dict[str, list[str]]:
    """Cached synonym map. Empty dict if tables missing / no data."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    try:
        data = _load_synonym_map_from_db()
    except Exception:
        # Migrations not applied yet, etc.
        data = {}
    cache.set(CACHE_KEY, data, CACHE_TTL_SECONDS)
    return data


def expand_term(term: str | None) -> list[str]:
    """
    Expand one term to itself + all synonyms in its group.
    Returns unique normalized strings (preserves original casing variants separately
    if caller also passes raw — here everything is normalized).
    """
    if not term:
        return []
    folded = normalize_search_text(term)
    if not folded:
        return []
    syn_map = get_synonym_map()
    related = syn_map.get(folded)
    if not related:
        return [folded]
    # Ensure self is first
    out = [folded]
    for s in related:
        if s != folded and s not in out:
            out.append(s)
    return out[:MAX_SYNONYMS_PER_TERM]


def expand_query_terms(query: str | None) -> list[str]:
    """
    All unique expanded terms for a full query (each token expanded).
    Used for OR-matching single-token / broad phrase search.
    """
    raw = sanitize_search_query(query)
    if not raw:
        return []
    tokens = tokenize_search_query(raw)
    if not tokens:
        folded = normalize_search_text(raw)
        return expand_term(folded) if folded else []

    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        for t in expand_term(token):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def expand_tokens_for_and_match(query: str | None) -> list[list[str]]:
    """
    For multi-word queries: each token becomes a list of synonym alternatives.
    Caller should AND between tokens and OR within each token's list.
    """
    raw = sanitize_search_query(query)
    if not raw:
        return []
    tokens = tokenize_search_query(raw)
    if not tokens:
        folded = normalize_search_text(raw)
        return [expand_term(folded)] if folded else []
    return [expand_term(t) for t in tokens]


def synonym_only_terms(query: str | None) -> list[str]:
    """
    Terms that appear only via synonym expansion (not in the typed query tokens).
    Used for lower ranking scores.
    """
    raw = sanitize_search_query(query)
    if not raw:
        return []
    tokens = set(tokenize_search_query(raw))
    folded = normalize_search_text(raw)
    if folded:
        tokens.add(folded)
    # also include multi-word folded whole as "typed"
    primary = {normalize_search_text(t) for t in tokens if t}
    expanded = expand_query_terms(raw)
    return [t for t in expanded if t not in primary]


# Seed data used by management command + data migration
# Terms that normalize to the same value are listed once (prefer diacritic form).
# Search still finds both "stap" and "štap" via normalize_search_text.
DEFAULT_SYNONYM_GROUPS: list[tuple[str, int, list[str]]] = [
    # (group name, priority, terms)
    ('Štap', 100, ['štap', 'rod', 'pecaljka']),
    ('Mašinica', 100, ['mašinica', 'reel', 'rolna']),
    ('Najlon', 90, ['najlon', 'struna', 'monofil', 'line']),
    ('Varalica', 90, ['varalica', 'vobler', 'lure']),
    ('Feeder', 95, ['feeder', 'fider']),
    ('Šaran', 80, ['šaran', 'carp']),
    ('Som', 80, ['som', 'catfish']),
    ('Smuđ', 80, ['smuđ', 'smud', 'zander']),  # smuđ→smudj; smud stays separate
    ('Štuka', 80, ['štuka', 'pike']),
    ('Šaranski', 85, ['šaranski']),
    ('Varaličarski', 85, ['varaličarski', 'spin', 'spinning']),
    ('Boila', 90, ['boila', 'boilie']),
    ('Udica', 70, ['udica', 'hook']),
    ('Plovak', 70, ['plovak', 'float']),
    ('Spod', 70, ['spod', 'spomb']),
    ('Hranilica', 70, ['hranilica', 'feeder hranilica']),
]


def seed_default_synonyms(*, clear_inactive: bool = False) -> dict[str, int]:
    """
    Create/update default fishing synonym groups.
    Idempotent: reuses groups by name, adds missing terms.
    """
    from EcommerceApp.models import SearchSynonym, SearchSynonymGroup
    from .normalize import normalize_search_text

    created_groups = 0
    created_terms = 0
    for naziv, prioritet, terms in DEFAULT_SYNONYM_GROUPS:
        group, g_created = SearchSynonymGroup.objects.get_or_create(
            naziv=naziv,
            defaults={'aktivno': True, 'prioritet': prioritet},
        )
        if g_created:
            created_groups += 1
        else:
            # Keep admin changes to prioritet unless brand new
            if not group.aktivno and clear_inactive:
                group.aktivno = True
                group.save(update_fields=['aktivno', 'azurirano'])
        existing = {
            (s.normalizovani_pojam or ''): s
            for s in group.sinonimi.all()
        }
        for pojam in terms:
            norm = normalize_search_text(pojam)
            if not norm:
                continue
            if norm in existing:
                continue
            SearchSynonym.objects.create(grupa=group, pojam=pojam)
            created_terms += 1
            existing[norm] = None

    invalidate_synonym_cache()
    return {
        'groups_created': created_groups,
        'terms_created': created_terms,
        'groups_total': SearchSynonymGroup.objects.count(),
        'terms_total': SearchSynonym.objects.count(),
    }
