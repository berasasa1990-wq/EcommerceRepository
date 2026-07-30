"""
PostgreSQL Full Text Search + pg_trgm fuzzy search za artikle.

Produkcija (Render / PostgreSQL):
  - Weighted tsvector (A/B/C/D) u koloni search_vector + GIN
  - SearchRank + TrigramSimilarity (pg_trgm)
  - search_document (denormalizirani tekst) + GIN trigram

Lokalni SQLite:
  - Fallback icontains na search_document / polja (isti API).
"""
from __future__ import annotations

import logging
import re

from django.db import connection, transaction
from django.db.models import Case, F, FloatField, Q, Value, When
from django.db.models.expressions import RawSQL
from django.db.models.functions import Coalesce, Greatest  # noqa: F401 — used in runtime search

logger = logging.getLogger(__name__)

TRGM_THRESHOLD = 0.18
TRGM_WEIGHT = 0.55
BOOST_EXACT_SIFRA = 2.5
BOOST_EXACT_NAZIV = 1.2
BOOST_NAZIV_PREFIX = 0.85
BOOST_SIFRA_PREFIX = 1.0

_DIACRITIC_MAP = str.maketrans({
    'š': 's', 'đ': 'd', 'č': 'c', 'ć': 'c', 'ž': 'z',
    'Š': 's', 'Đ': 'd', 'Č': 'c', 'Ć': 'c', 'Ž': 'z',
})

# Table name for raw SQL (Django default app_label_model)
PRODUCT_TABLE = 'EcommerceApp_product'


def is_postgres() -> bool:
    return connection.vendor == 'postgresql'


def normalize_query(query: str) -> str:
    if not query:
        return ''
    return re.sub(r'\s+', ' ', str(query).strip())


def fold_diacritics(value: str) -> str:
    if not value:
        return ''
    return str(value).casefold().translate(_DIACRITIC_MAP)


def build_search_document(product) -> str:
    """Denormalizirani tekst: šifra, naziv, brend, kat, tagovi, opis, var. šifre."""
    parts: list[str] = []
    if product.sifra:
        parts.append(str(product.sifra))
    if product.naziv:
        parts.append(str(product.naziv))
    if getattr(product, 'barkod', None):
        parts.append(str(product.barkod))

    brend = getattr(product, 'brend', None)
    if brend is not None and getattr(brend, 'naziv', None):
        parts.append(brend.naziv)

    cat = getattr(product, 'kategorija', None)
    if cat is not None:
        if getattr(cat, 'naziv', None):
            parts.append(cat.naziv)
        if getattr(cat, 'search_tagovi', None):
            parts.append(cat.search_tagovi)
        parent = getattr(cat, 'roditelj', None)
        if parent is not None:
            if getattr(parent, 'naziv', None):
                parts.append(parent.naziv)
            if getattr(parent, 'search_tagovi', None):
                parts.append(parent.search_tagovi)

    if product.opis:
        parts.append(str(product.opis)[:2000])

    try:
        for v in product.varijacije.all():
            if getattr(v, 'sifra', None):
                parts.append(str(v.sifra))
    except Exception:
        pass

    folded = [fold_diacritics(p) for p in parts if p]
    combined = ' '.join(list(parts) + folded)
    return re.sub(r'\s+', ' ', combined).strip()


def _tsvector_sql_for_row() -> str:
    """
    Weighted tsvector expression for one product row (aliases via product table).
    Priority: A=šifra, B=naziv, C=brend/kategorija/tagovi, D=opis/roditelj.
    """
    return """
    (
      setweight(to_tsvector('simple', coalesce(p.sifra, '')), 'A')
      || setweight(to_tsvector('simple', coalesce(p.naziv, '')), 'B')
      || setweight(to_tsvector('simple', coalesce(b.naziv, '')), 'C')
      || setweight(to_tsvector('simple', coalesce(c.naziv, '')), 'C')
      || setweight(to_tsvector('simple', coalesce(c.search_tagovi, '')), 'C')
      || setweight(to_tsvector('simple', coalesce(parent.naziv, '')), 'D')
      || setweight(to_tsvector('simple', coalesce(p.opis, '')), 'D')
      || setweight(to_tsvector('simple', coalesce(p.search_document, '')), 'C')
    )
    """


@transaction.atomic
def update_product_search_index(product_or_id) -> None:
    """Ažuriraj search_document (+ search_vector na PostgreSQL)."""
    from .models import Product

    pk = product_or_id.pk if hasattr(product_or_id, 'pk') else product_or_id
    product = (
        Product.objects
        .select_related('brend', 'kategorija', 'kategorija__roditelj')
        .prefetch_related('varijacije')
        .filter(pk=pk)
        .first()
    )
    if product is None:
        return

    document = build_search_document(product)
    Product.objects.filter(pk=product.pk).update(search_document=document)

    if not is_postgres() or not _search_vector_column_exists():
        return

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE "{PRODUCT_TABLE}" AS p
            SET search_vector = (
              setweight(to_tsvector('simple', coalesce(p.sifra, '')), 'A')
              || setweight(to_tsvector('simple', coalesce(p.naziv, '')), 'B')
              || setweight(to_tsvector('simple', coalesce(b.naziv, '')), 'C')
              || setweight(to_tsvector('simple', coalesce(c.naziv, '')), 'C')
              || setweight(to_tsvector('simple', coalesce(c.search_tagovi, '')), 'C')
              || setweight(to_tsvector('simple', coalesce(parent.naziv, '')), 'D')
              || setweight(to_tsvector('simple', coalesce(p.opis, '')), 'D')
              || setweight(to_tsvector('simple', coalesce(p.search_document, '')), 'C')
            )
            FROM "{PRODUCT_TABLE}" AS p2
            LEFT JOIN "EcommerceApp_brand" AS b ON b.id = p2.brend_id
            LEFT JOIN "EcommerceApp_category" AS c ON c.id = p2.kategorija_id
            LEFT JOIN "EcommerceApp_category" AS parent ON parent.id = c.roditelj_id
            WHERE p.id = p2.id AND p.id = %s
            """,
            [product.pk],
        )


def _search_vector_column_exists() -> bool:
    if not is_postgres():
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = %s AND column_name = 'search_vector'
                """,
                [PRODUCT_TABLE],
            )
            return cursor.fetchone() is not None
    except Exception:
        return False


def rebuild_all_product_search_indexes(*, batch_size: int = 250) -> int:
    """Reindex svih artikala. Vraća broj obrađenih."""
    from .models import Product

    qs = (
        Product.objects
        .select_related('brend', 'kategorija', 'kategorija__roditelj')
        .prefetch_related('varijacije')
        .order_by('pk')
    )
    count = 0
    ids = []
    for product in qs.iterator(chunk_size=batch_size):
        document = build_search_document(product)
        Product.objects.filter(pk=product.pk).update(search_document=document)
        ids.append(product.pk)
        count += 1
        if len(ids) >= batch_size:
            _bulk_update_search_vectors(ids)
            ids = []
    if ids:
        _bulk_update_search_vectors(ids)
    logger.info('Product search index rebuilt for %s products', count)
    return count


def _bulk_update_search_vectors(ids: list[int]) -> None:
    if not ids or not is_postgres() or not _search_vector_column_exists():
        return
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            UPDATE "{PRODUCT_TABLE}" AS p
            SET search_vector = (
              setweight(to_tsvector('simple', coalesce(p.sifra, '')), 'A')
              || setweight(to_tsvector('simple', coalesce(p.naziv, '')), 'B')
              || setweight(to_tsvector('simple', coalesce(b.naziv, '')), 'C')
              || setweight(to_tsvector('simple', coalesce(c.naziv, '')), 'C')
              || setweight(to_tsvector('simple', coalesce(c.search_tagovi, '')), 'C')
              || setweight(to_tsvector('simple', coalesce(parent.naziv, '')), 'D')
              || setweight(to_tsvector('simple', coalesce(p.opis, '')), 'D')
              || setweight(to_tsvector('simple', coalesce(p.search_document, '')), 'C')
            )
            FROM "{PRODUCT_TABLE}" AS p2
            LEFT JOIN "EcommerceApp_brand" AS b ON b.id = p2.brend_id
            LEFT JOIN "EcommerceApp_category" AS c ON c.id = p2.kategorija_id
            LEFT JOIN "EcommerceApp_category" AS parent ON parent.id = c.roditelj_id
            WHERE p.id = p2.id AND p.id = ANY(%s)
            """,
            [ids],
        )


def apply_product_search(products_qs, query: str):
    """
    Glavni ulaz pretrage. Anotira search_score (veći = bolje) na PostgreSQL.
    """
    raw = normalize_query(query)
    if not raw:
        return products_qs
    if len(raw) < 2:
        return products_qs.none()

    if is_postgres() and _search_vector_column_exists():
        try:
            return _postgres_full_text_search(products_qs, raw)
        except Exception:
            logger.exception('PostgreSQL FTS failed — fallback')
            return _fallback_icontains_search(products_qs, raw)
    if is_postgres():
        # PG bez kolone (migracija nije prošla) — runtime SearchVector
        try:
            return _postgres_runtime_search(products_qs, raw)
        except Exception:
            logger.exception('PostgreSQL runtime FTS failed — fallback')
            return _fallback_icontains_search(products_qs, raw)
    return _fallback_icontains_search(products_qs, raw)


def _postgres_runtime_search(products_qs, query: str):
    """FTS bez spremljene kolone (SearchVector + SearchRank + Trigram)."""
    from django.contrib.postgres.search import (
        SearchQuery,
        SearchRank,
        SearchVector,
        TrigramSimilarity,
    )

    vector = (
        SearchVector('sifra', weight='A', config='simple')
        + SearchVector('naziv', weight='B', config='simple')
        + SearchVector('brend__naziv', weight='C', config='simple')
        + SearchVector('kategorija__naziv', weight='C', config='simple')
        + SearchVector('kategorija__search_tagovi', weight='C', config='simple')
        + SearchVector('kategorija__roditelj__naziv', weight='D', config='simple')
        + SearchVector('opis', weight='D', config='simple')
        + SearchVector('search_document', weight='C', config='simple')
    )
    search_query = SearchQuery(query, config='simple', search_type='websearch')
    q_folded = fold_diacritics(query)
    if q_folded and q_folded != query.casefold():
        search_query = search_query | SearchQuery(
            q_folded, config='simple', search_type='websearch',
        )

    qs = products_qs.annotate(
        _sv=vector,
        fts_rank=Coalesce(SearchRank(F('_sv'), search_query), Value(0.0)),
        trgm_sim=Greatest(
            Coalesce(TrigramSimilarity('naziv', query), Value(0.0)),
            Coalesce(TrigramSimilarity('sifra', query), Value(0.0)),
            Coalesce(TrigramSimilarity('search_document', query), Value(0.0)),
            Coalesce(TrigramSimilarity('brend__naziv', query), Value(0.0)),
            Value(0.0),
            output_field=FloatField(),
        ),
    )
    return _annotate_boosts_and_filter(qs, query, fts_filter=Q(_sv=search_query))


def _postgres_full_text_search(products_qs, query: str):
    """FTS preko spremljenog search_vector + pg_trgm na search_document/naziv/šifra."""
    from django.contrib.postgres.search import TrigramSimilarity

    q_folded = fold_diacritics(query)

    # ts_rank + match na spremljenom tsvector (qualified table name zbog joinova)
    tbl = PRODUCT_TABLE
    fts_rank = RawSQL(
        f'COALESCE(ts_rank("{tbl}".search_vector, websearch_to_tsquery(\'simple\', %s)), 0)',
        (query,),
        output_field=FloatField(),
    )
    fts_match_sql = RawSQL(
        f'CASE WHEN "{tbl}".search_vector @@ websearch_to_tsquery(\'simple\', %s) THEN 1 ELSE 0 END',
        (query,),
        output_field=FloatField(),
    )

    qs = products_qs.annotate(
        fts_rank=fts_rank,
        fts_match=fts_match_sql,
        trgm_sim=Greatest(
            Coalesce(TrigramSimilarity('naziv', query), Value(0.0)),
            Coalesce(TrigramSimilarity('sifra', query), Value(0.0)),
            Coalesce(TrigramSimilarity('search_document', query), Value(0.0)),
            Coalesce(TrigramSimilarity('brend__naziv', query), Value(0.0)),
            Value(0.0),
            output_field=FloatField(),
        ),
    )
    fts_filter = Q(fts_match=1)
    if q_folded and q_folded != query.casefold():
        # drugi prolaz s folded upitom preko document/trigram (već u trgm / icontains)
        pass
    return _annotate_boosts_and_filter(qs, query, fts_filter=fts_filter)


def _annotate_boosts_and_filter(qs, query: str, *, fts_filter: Q):
    q_folded = fold_diacritics(query)
    qs = qs.annotate(
        exact_sifra=Case(
            When(sifra__iexact=query, then=Value(BOOST_EXACT_SIFRA)),
            default=Value(0.0),
            output_field=FloatField(),
        ),
        exact_naziv=Case(
            When(naziv__iexact=query, then=Value(BOOST_EXACT_NAZIV)),
            default=Value(0.0),
            output_field=FloatField(),
        ),
        prefix_naziv=Case(
            When(naziv__istartswith=query, then=Value(BOOST_NAZIV_PREFIX)),
            default=Value(0.0),
            output_field=FloatField(),
        ),
        prefix_sifra=Case(
            When(sifra__istartswith=query, then=Value(BOOST_SIFRA_PREFIX)),
            default=Value(0.0),
            output_field=FloatField(),
        ),
    ).annotate(
        search_score=(
            F('fts_rank')
            + F('trgm_sim') * Value(TRGM_WEIGHT)
            + F('exact_sifra')
            + F('exact_naziv')
            + F('prefix_naziv')
            + F('prefix_sifra')
        ),
    )

    match = (
        fts_filter
        | Q(trgm_sim__gte=TRGM_THRESHOLD)
        | Q(sifra__icontains=query)
        | Q(naziv__icontains=query)
        | Q(varijacije__sifra__icontains=query)
        | Q(search_document__icontains=query)
    )
    if q_folded and q_folded != query.casefold():
        match |= (
            Q(naziv__icontains=q_folded)
            | Q(sifra__icontains=q_folded)
            | Q(search_document__icontains=q_folded)
        )

    return (
        qs.filter(match)
        .distinct()
        .order_by('-search_score', '-prioritet_lagera', 'naziv')
    )


def _fallback_icontains_search(products_qs, query: str):
    """SQLite / emergency fallback."""
    raw = query
    folded = fold_diacritics(raw)
    match = (
        Q(naziv__icontains=raw)
        | Q(sifra__icontains=raw)
        | Q(varijacije__sifra__icontains=raw)
        | Q(brend__naziv__icontains=raw)
        | Q(kategorija__naziv__icontains=raw)
        | Q(kategorija__roditelj__naziv__icontains=raw)
        | Q(kategorija__search_tagovi__icontains=raw)
        | Q(opis__icontains=raw)
        | Q(search_document__icontains=raw)
    )
    if folded and folded != raw.casefold():
        match |= (
            Q(naziv__icontains=folded)
            | Q(sifra__icontains=folded)
            | Q(search_document__icontains=folded)
            | Q(kategorija__search_tagovi__icontains=folded)
        )
    return products_qs.filter(match).distinct()


def order_search_results(products, *, query='', price_sort=None):
    """Sort liste proizvoda (search_score ili python score)."""
    if not products:
        return products

    q = normalize_query(query)

    def lager_prio(p):
        try:
            return int(getattr(p, 'prioritet_lagera', 0) or 0)
        except (TypeError, ValueError):
            return 0

    def score(p):
        if getattr(p, 'search_score', None) is not None:
            try:
                return float(p.search_score)
            except (TypeError, ValueError):
                pass
        return _python_relevance_score(p, q)

    def price_of(p):
        try:
            variations = list(p.varijacije.all())
            if variations:
                return float(min(v.prikazna_cijena for v in variations) or 0)
            return float(p.prikazna_cijena or 0)
        except Exception:
            return 0.0

    reverse_price = price_sort == 'opadajuca'

    def key(p):
        sc = -score(p)
        prio = -lager_prio(p)
        price = price_of(p)
        name = (p.naziv or '').lower()
        if reverse_price:
            return (sc, prio, -price, name)
        return (sc, prio, price, name)

    return sorted(products, key=key)


def _python_relevance_score(product, query: str) -> float:
    if not query:
        return 0.0
    q = query.casefold()
    score = 0.0
    sifra = (product.sifra or '').casefold()
    naziv = (product.naziv or '').casefold()
    if sifra == q:
        score += 100
    elif sifra.startswith(q):
        score += 70
    elif q in sifra:
        score += 40
    if naziv == q:
        score += 60
    elif naziv.startswith(q):
        score += 45
    elif q in naziv:
        score += 25
    return score
