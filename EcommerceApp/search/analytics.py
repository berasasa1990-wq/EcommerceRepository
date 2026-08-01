"""
Search analytics — lightweight logging that must not slow the main search path.

Rules:
- Never log each autocomplete keystroke.
- Log full results page (page 1), form submit (same page), autocomplete product click.
- No IP / geo / PII beyond optional authenticated user FK + session_key.
- Failures are swallowed so analytics never break checkout/search.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Count, Q

from .normalize import normalize_search_text, sanitize_search_query

logger = logging.getLogger(__name__)

SESSION_LOG_IDS_KEY = 'search_analytics_log_ids'
SESSION_LOG_IDS_MAX = 8
LOW_RESULT_THRESHOLD = 3


def _ensure_session(request) -> str:
    try:
        if not request.session.session_key:
            request.session.save()
        return request.session.session_key or ''
    except Exception:
        return ''


def _remember_log_id(request, log_id: int) -> None:
    try:
        ids = list(request.session.get(SESSION_LOG_IDS_KEY) or [])
        ids = [log_id] + [i for i in ids if i != log_id]
        request.session[SESSION_LOG_IDS_KEY] = ids[:SESSION_LOG_IDS_MAX]
        request.session.modified = True
    except Exception:
        pass


def get_recent_search_log_ids(request) -> list[int]:
    try:
        raw = request.session.get(SESSION_LOG_IDS_KEY) or []
        return [int(x) for x in raw if x]
    except Exception:
        return []


def log_search_query(
    request,
    query: str,
    *,
    result_count: int = 0,
    source: str = 'full_page',
    selected_suggestion: str = '',
):
    """
    Create SearchQueryLog. Call AFTER results are ready (single INSERT).
    Returns the log instance or None on failure / empty query.
    Never raises to the caller.
    """
    from EcommerceApp.models import SearchQueryLog

    original = sanitize_search_query(query)
    if not original:
        return None
    try:
        session_key = _ensure_session(request)
        user = None
        if getattr(request, 'user', None) and request.user.is_authenticated:
            user = request.user
        valid_sources = {c.value for c in SearchQueryLog.Source}
        if source not in valid_sources:
            source = SearchQueryLog.Source.FULL_PAGE
        log = SearchQueryLog.objects.create(
            original_query=original[:150],
            normalized_query=normalize_search_text(original)[:150],
            result_count=max(0, int(result_count or 0)),
            user=user,
            session_key=session_key[:40],
            source=source,
            selected_suggestion=(selected_suggestion or '')[:150],
        )
        _remember_log_id(request, log.pk)
        return log
    except Exception:
        logger.exception('search analytics log_search_query failed')
        return None


def log_search_click(
    request,
    *,
    product_id: int | None,
    result_position: int = 0,
    query: str = '',
    search_log_id: int | None = None,
    source: str = 'full_page',
):
    """
    Record product click from search/autocomplete.
    Creates a SearchQueryLog if none provided (autocomplete path).
    """
    from EcommerceApp.models import Product, SearchClickLog, SearchQueryLog

    try:
        log = None
        if search_log_id:
            log = SearchQueryLog.objects.filter(pk=search_log_id).first()

        if log is None:
            # Prefer most recent session log matching query
            session_key = _ensure_session(request)
            qs = SearchQueryLog.objects.filter(session_key=session_key).order_by('-created_at')
            if query:
                norm = normalize_search_text(sanitize_search_query(query))
                log = qs.filter(normalized_query=norm).first() or qs.first()
            else:
                log = qs.first()

        if log is None and query:
            log = log_search_query(
                request,
                query,
                result_count=0,
                source=source or SearchQueryLog.Source.AUTOCOMPLETE,
            )

        if log is None:
            return None

        product = None
        if product_id:
            product = Product.objects.filter(pk=product_id, aktivan=True).only('id').first()

        pos = max(0, min(int(result_position or 0), 999))
        click = SearchClickLog.objects.create(
            search_query=log,
            product=product,
            result_position=pos,
        )
        _remember_log_id(request, log.pk)
        return click
    except Exception:
        logger.exception('search analytics log_search_click failed')
        return None


def mark_search_converted_to_cart(request) -> int:
    """Flag recent session search logs as converted_to_cart. Returns updated count."""
    from EcommerceApp.models import SearchQueryLog

    try:
        ids = get_recent_search_log_ids(request)
        session_key = _ensure_session(request)
        qs = SearchQueryLog.objects.filter(converted_to_cart=False)
        if ids:
            qs = qs.filter(Q(pk__in=ids) | Q(session_key=session_key))
        elif session_key:
            qs = qs.filter(session_key=session_key)
        else:
            return 0
        # Only recent window: last 50 for session
        qs = qs.order_by('-created_at')[:50]
        pks = list(qs.values_list('pk', flat=True))
        if not pks:
            return 0
        return SearchQueryLog.objects.filter(pk__in=pks, converted_to_cart=False).update(
            converted_to_cart=True,
        )
    except Exception:
        logger.exception('search analytics mark cart failed')
        return 0


def mark_search_converted_to_order(request) -> int:
    """Flag recent session search logs as converted_to_order (+ cart)."""
    from EcommerceApp.models import SearchQueryLog

    try:
        ids = get_recent_search_log_ids(request)
        session_key = _ensure_session(request)
        qs = SearchQueryLog.objects.filter(converted_to_order=False)
        if ids:
            qs = qs.filter(Q(pk__in=ids) | Q(session_key=session_key))
        elif session_key:
            qs = qs.filter(session_key=session_key)
        else:
            return 0
        pks = list(qs.order_by('-created_at')[:50].values_list('pk', flat=True))
        if not pks:
            return 0
        return SearchQueryLog.objects.filter(pk__in=pks).update(
            converted_to_order=True,
            converted_to_cart=True,
        )
    except Exception:
        logger.exception('search analytics mark order failed')
        return 0


# ---------------------------------------------------------------------------
# Admin analytics helpers
# ---------------------------------------------------------------------------

def top_queries(*, limit: int = 50, days: int | None = None):
    from django.utils import timezone
    from datetime import timedelta
    from EcommerceApp.models import SearchQueryLog

    qs = SearchQueryLog.objects.all()
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return (
        qs.values('normalized_query')
        .annotate(
            hits=Count('id'),
            avg_results=Count('id'),  # placeholder replaced below
            zero_hits=Count('id', filter=Q(result_count=0)),
            cart_hits=Count('id', filter=Q(converted_to_cart=True)),
            order_hits=Count('id', filter=Q(converted_to_order=True)),
        )
        .order_by('-hits')[:limit]
    )


def top_queries_annotated(*, limit: int = 50, days: int | None = None):
    from django.db.models import Avg
    from django.utils import timezone
    from datetime import timedelta
    from EcommerceApp.models import SearchQueryLog

    qs = SearchQueryLog.objects.all()
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return list(
        qs.exclude(normalized_query='')
        .values('normalized_query')
        .annotate(
            hits=Count('id'),
            avg_results=Avg('result_count'),
            zero_hits=Count('id', filter=Q(result_count=0)),
            cart_hits=Count('id', filter=Q(converted_to_cart=True)),
            order_hits=Count('id', filter=Q(converted_to_order=True)),
        )
        .order_by('-hits')[:limit]
    )


def zero_result_queries(*, limit: int = 50, days: int | None = None):
    from django.utils import timezone
    from datetime import timedelta
    from EcommerceApp.models import SearchQueryLog

    qs = SearchQueryLog.objects.filter(result_count=0)
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return list(
        qs.exclude(normalized_query='')
        .values('normalized_query', 'original_query')
        .annotate(hits=Count('id'))
        .order_by('-hits')[:limit]
    )


def low_result_queries(*, limit: int = 50, max_results: int = LOW_RESULT_THRESHOLD, days: int | None = None):
    from django.utils import timezone
    from datetime import timedelta
    from django.db.models import Avg
    from EcommerceApp.models import SearchQueryLog

    qs = SearchQueryLog.objects.filter(result_count__gt=0, result_count__lte=max_results)
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return list(
        qs.exclude(normalized_query='')
        .values('normalized_query')
        .annotate(hits=Count('id'), avg_results=Avg('result_count'))
        .order_by('-hits')[:limit]
    )


def top_clicked_products(*, limit: int = 50, days: int | None = None):
    from django.utils import timezone
    from datetime import timedelta
    from EcommerceApp.models import SearchClickLog

    qs = SearchClickLog.objects.exclude(product__isnull=True)
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return list(
        qs.values('product_id', 'product__naziv', 'product__sifra')
        .annotate(
            clicks=Count('id'),
            avg_position=Count('id'),  # filled properly below
        )
        .order_by('-clicks')[:limit]
    )


def top_clicked_products_annotated(*, limit: int = 50, days: int | None = None):
    from django.db.models import Avg
    from django.utils import timezone
    from datetime import timedelta
    from EcommerceApp.models import SearchClickLog

    qs = SearchClickLog.objects.exclude(product__isnull=True)
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return list(
        qs.values('product_id', 'product__naziv', 'product__sifra')
        .annotate(clicks=Count('id'), avg_position=Avg('result_position'))
        .order_by('-clicks')[:limit]
    )


def top_converting_queries(*, limit: int = 50, days: int | None = None):
    from django.utils import timezone
    from datetime import timedelta
    from EcommerceApp.models import SearchQueryLog

    qs = SearchQueryLog.objects.filter(converted_to_order=True)
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    return list(
        qs.exclude(normalized_query='')
        .values('normalized_query')
        .annotate(
            hits=Count('id'),
            order_hits=Count('id', filter=Q(converted_to_order=True)),
            cart_hits=Count('id', filter=Q(converted_to_cart=True)),
        )
        .order_by('-order_hits', '-cart_hits')[:limit]
    )
