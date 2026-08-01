"""
Basic product search for EcommerceApp (no separate Django app).

- Multi-field ORM search + text normalization
- SQLite (local) and PostgreSQL (Render)
- No Elasticsearch / Algolia / Meilisearch / Typesense
- No fuzzy / synonyms / analytics / AI in this layer
"""

from .normalize import (
    MAX_QUERY_LENGTH,
    normalize_measurements,
    normalize_search_text,
    sanitize_search_query,
    tokenize_search_query,
)
from .query import apply_search_filter, search_product_queryset, search_products_ranked
from .ranking import (
    SCORE,
    annotate_search_relevance,
    apply_search_ranked,
    order_search_queryset,
    score_product,
    sort_products_for_search,
)
from .suggest import SEARCH_SUGGEST_LIMIT, build_suggest_response
from .fuzzy import (
    expand_queryset_with_fuzzy,
    fuzzy_product_ids,
    is_postgres,
    suggest_did_you_mean,
)
from .intent import (
    invalidate_intent_cache,
    match_intent_rules,
    resolve_intent_recommendations,
)
from .measures import parse_measures_from_text, strip_measures_from_text
from .synonyms import (
    expand_query_terms,
    expand_term,
    get_synonym_map,
    invalidate_synonym_cache,
    seed_default_synonyms,
)

__all__ = [
    'MAX_QUERY_LENGTH',
    'SCORE',
    'SEARCH_SUGGEST_LIMIT',
    'annotate_search_relevance',
    'apply_search_filter',
    'apply_search_ranked',
    'build_suggest_response',
    'expand_query_terms',
    'expand_queryset_with_fuzzy',
    'expand_term',
    'fuzzy_product_ids',
    'get_synonym_map',
    'invalidate_intent_cache',
    'invalidate_synonym_cache',
    'is_postgres',
    'match_intent_rules',
    'resolve_intent_recommendations',
    'normalize_measurements',
    'normalize_search_text',
    'order_search_queryset',
    'parse_measures_from_text',
    'sanitize_search_query',
    'score_product',
    'search_product_queryset',
    'search_products_ranked',
    'seed_default_synonyms',
    'sort_products_for_search',
    'strip_measures_from_text',
    'suggest_did_you_mean',
    'tokenize_search_query',
]
