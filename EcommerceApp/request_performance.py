"""Memoize repeated reads only for the lifetime of one safe request."""
from contextvars import ContextVar
import logging
from time import perf_counter
from django.db import connection

logger = logging.getLogger(__name__)

_reads = ContextVar('performance_request_reads', default=None)


def request_read(key, loader):
    reads = _reads.get()
    if reads is None:
        return loader()
    if key not in reads:
        reads[key] = loader()
    return reads[key]


def schema_tables():
    return request_read(('schema-tables', connection.alias), connection.introspection.table_names)


class RequestReadMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _reads.set({} if request.method in ('GET', 'HEAD') else None)
        try:
            if request.path.startswith(('/panel/', '/wms/')) or request.path == '/panel':
                return self._measure_workspace(request)
            return self.get_response(request)
        finally:
            _reads.reset(token)

    def _measure_workspace(self, request):
        count = 0
        sql_seconds = 0.0

        def measure(execute, sql, params, many, context):
            nonlocal count, sql_seconds
            count += 1
            started = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                sql_seconds += perf_counter() - started

        started = perf_counter()
        with connection.execute_wrapper(measure):
            response = self.get_response(request)
        elapsed_ms = (perf_counter() - started) * 1000
        sql_ms = sql_seconds * 1000
        if elapsed_ms >= 1000:
            # Never log query parameters, SQL, contacts or request bodies.
            logger.warning(
                'Slow workspace method=%s path=%s status=%s total_ms=%.1f sql_execute_ms=%.1f sql_count=%s',
                request.method, request.path, response.status_code, elapsed_ms, sql_ms, count,
            )
        user = getattr(request, 'user', None)
        if user and user.is_authenticated and user.is_superuser:
            # Aggregate timing only; includes middleware, connection setup and render.
            timing = f'app;dur={elapsed_ms:.1f}, sql;dur={sql_ms:.1f};desc="{count} queries"'
            previous = response.get('Server-Timing')
            response['Server-Timing'] = f'{previous}, {timing}' if previous else timing
        return response
