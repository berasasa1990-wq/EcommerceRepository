"""Memoize repeated reads only for the lifetime of one safe request."""
from contextvars import ContextVar
from django.db import connection

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
            return self.get_response(request)
        finally:
            _reads.reset(token)
