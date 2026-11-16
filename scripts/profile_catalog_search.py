"""Read-only search profile. Run via manage.py shell (see docs/search-ranking.md)."""
import json
from time import perf_counter

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import RequestFactory
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from EcommerceApp.models import Product
from EcommerceApp.views import _apply_search_filter, _rank_search_queryset, search_suggest


def run(queries=('masinica', 'MT12221', 'wintr boots')):
    print('backend:', connection.vendor, 'catalogue:', Product.objects.count())
    for query in queries:
        started = perf_counter()
        with CaptureQueriesContext(connection) as captured:
            qs = _rank_search_queryset(_apply_search_filter(
                Product.objects.filter(aktivan=True, sakriven_do_stanja=False), query,
            ), query).values_list('pk', 'naziv')[:6]
            rows = list(qs)
        print(json.dumps({'q': query, 'queries': len(captured),
            'milliseconds': round((perf_counter() - started) * 1000, 2),
            'rows': rows}, ensure_ascii=False))
        if connection.vendor == 'postgresql':
            print(qs.explain(analyze=True, buffers=True))
            with connection.cursor() as cursor:
                cursor.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN ('EcommerceApp_product', 'EcommerceApp_productvariation')")
                for index in cursor.fetchall():
                    print(index)
        elif connection.vendor == 'sqlite':
            sql, params = qs.query.sql_with_params()
            with connection.cursor() as cursor:
                cursor.execute('EXPLAIN QUERY PLAN ' + sql, params)
                for step in cursor.fetchall():
                    print(step)
        for executed in captured:
            print('SQL:', executed['sql'])
        request = RequestFactory().get('/api/pretraga/', {'q': query})
        request.user = AnonymousUser()
        request.session = SessionStore()
        started = perf_counter()
        with CaptureQueriesContext(connection) as api_queries:
            data = json.loads(search_suggest(request).content)
        print(json.dumps({'api_q': query, 'queries': len(api_queries),
            'milliseconds': round((perf_counter() - started) * 1000, 2),
            'results': len(data['results'])}, ensure_ascii=False))
