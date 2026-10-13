"""Opt-in aggregate request timing. Never retain SQL, parameters or customer data."""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import logging
from time import perf_counter
from uuid import uuid4

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)


@dataclass
class Timings:
    sql_seconds: float = 0.0
    sql_count: int = 0
    connect_seconds: float = 0.0
    connect_count: int = 0

    def execute(self, execute, sql, params, many, context):
        started = perf_counter()
        self.sql_count += 1
        try:
            return execute(sql, params, many, context)
        finally:
            self.sql_seconds += perf_counter() - started


@contextmanager
def measure_connections(database, timings):
    """Wrap only this thread's DatabaseWrapper; don't open or close a connection."""
    original = database.get_new_connection
    sentinel = object()
    previous = database.__dict__.get('get_new_connection', sentinel)

    def connect(*args, **kwargs):
        started = perf_counter()
        timings.connect_count += 1
        try:
            return original(*args, **kwargs)
        finally:
            timings.connect_seconds += perf_counter() - started

    database.get_new_connection = connect
    try:
        with database.execute_wrapper(timings.execute):
            yield
    finally:
        if previous is sentinel:
            del database.get_new_connection
        else:
            database.get_new_connection = previous


class TTFBDiagnosticsMiddleware:
    EXCLUDED = ('/admin', '/panel', '/wms', '/nalog', '/api', '/static', '/media', '/veleprodaja')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or '/'
        if (not getattr(settings, 'TTFB_DIAGNOSTICS_ENABLED', False)
                or request.method not in ('GET', 'HEAD')
                or any(path == prefix or path.startswith(prefix + '/') for prefix in self.EXCLUDED)):
            return self.get_response(request)
        request_id = uuid4().hex
        request.ttfb_request_id = request_id
        timings = Timings()
        started = perf_counter()
        # DatabaseWrapper instances are thread-local in this synchronous WSGI app.
        # No global driver patch; no eager connection or health-check query.
        with ExitStack() as stack:
            for database in connections.all():
                stack.enter_context(measure_connections(database, timings))
            response = self.get_response(request)
        app_ms = (perf_counter() - started) * 1000
        sql_ms = timings.sql_seconds * 1000
        connect_ms = timings.connect_seconds * 1000
        metrics = (f'django;dur={app_ms:.2f}, sql;dur={sql_ms:.2f};desc="{timings.sql_count} queries", '
                   f'db_connect;dur={connect_ms:.2f};desc="{timings.connect_count} attempts"')
        previous = response.get('Server-Timing')
        response['Server-Timing'] = f'{previous}, {metrics}' if previous else metrics
        response['X-TTFB-Request-ID'] = request_id
        if app_ms > 1000:
            # Use a code-defined route name, never URL/query strings, bodies or headers.
            match = getattr(request, 'resolver_match', None)
            route = getattr(match, 'url_name', None) or 'unresolved'
            logger.warning(
                'Slow Django request request_id=%s route=%s method=%s status=%s '
                'django_ms=%.2f sql_execute_ms=%.2f sql_count=%s db_connect_ms=%.2f db_connect_count=%s',
                request_id, route, request.method, response.status_code,
                app_ms, sql_ms, timings.sql_count, connect_ms, timings.connect_count,
            )
        return response
