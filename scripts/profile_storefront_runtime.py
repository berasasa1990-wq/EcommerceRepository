"""Local cold/warm audit only, with all synthetic-session writes rolled back.

Use carp_runtime_qa_settings and a copied /private/tmp/carp-audit-*.sqlite3.
Never initializes production settings or outputs SQL/parameters/customer data.
"""
import argparse
import json
import logging
import os
import platform
import random
import resource
import statistics
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--out', required=True)
parser.add_argument('--samples', type=int, default=5)
args = parser.parse_args()
if not 2 <= args.samples <= 10:
    raise SystemExit('Use two to ten local samples')
if os.environ.get('DJANGO_SETTINGS_MODULE') != 'carp_runtime_qa_settings':
    raise SystemExit('Synthetic runtime QA settings required')

import django
from django.conf import settings

if (settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3'
        or not str(settings.DATABASES['default']['NAME']).startswith('/private/tmp/carp-audit-')):
    raise SystemExit('Only an isolated synthetic SQLite copy is allowed')
logging.disable(logging.CRITICAL)
django.setup()
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, transaction
from django.test import Client
from django.template.backends.django import Template
from EcommerceApp.cart import Cart
from EcommerceApp.models import Product, Category
from django.core.handlers.base import BaseHandler


def rss_peak_mb():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if platform.system() == 'Darwin' else 1024), 2)


results = {'database': 'synthetic SQLite', 'samples_per_route': args.samples,
           'peak_rss_mb_after_import': rss_peak_mb(), 'routes': {}}
with transaction.atomic():
    public, staff, cart_client = Client(), Client(), Client()
    staff.force_login(get_user_model().objects.filter(is_superuser=True).first())
    product = Product.objects.filter(aktivan=True, na_stanju=True, stanje__gt=0).first()
    category = Category.objects.filter(aktivan=True).first()
    session = cart_client.session
    # Exercise a populated cart and checkout without creating an order.
    from types import SimpleNamespace
    Cart(SimpleNamespace(session=session)).add(product)
    session.save()
    routes = [('/', public), ('/?q=QA', public),
              (category.get_absolute_url(), public), (product.get_absolute_url(), public),
              ('/korpa/', cart_client), ('/narudzba/', cart_client), ('/nalog/', staff),
              ('/admin/', staff), ('/panel', staff), ('/wms/', staff),
              ('/wms/zalihe/?q=QA', staff), ('/wms/narudzbe/', staff)]
    for path, client in routes:
        cache.clear()
        rows = []
        for sample in range(args.samples):
            sql_durations, render_durations, view_durations = [], [], []
            random.seed(321)
            def measure_sql(execute, sql, params, many, context):
                start = time.perf_counter()
                try:
                    return execute(sql, params, many, context)
                finally:
                    sql_durations.append((time.perf_counter() - start) * 1000)

            original_render = Template.render
            def measure_render(self, *positional, **keywords):
                start = time.perf_counter()
                try:
                    return original_render(self, *positional, **keywords)
                finally:
                    render_durations.append((time.perf_counter() - start) * 1000)

            original_get_response = BaseHandler._get_response
            def measure_view(self, request):
                begin = time.perf_counter()
                try:
                    return original_get_response(self, request)
                finally:
                    view_durations.append((time.perf_counter() - begin) * 1000)

            with connection.execute_wrapper(measure_sql), ExitStack() as stack:
                stack.enter_context(patch.object(Template, 'render', measure_render))
                stack.enter_context(patch.object(BaseHandler, '_get_response', measure_view))
                # Rebuild only this synthetic test client's middleware bindings.
                client.handler.load_middleware()
                # Middleware construction/static indexing belongs to app startup,
                # not request TTFB. Keep it outside each request sample.
                sql_durations.clear()
                render_durations.clear()
                view_durations.clear()
                start = time.perf_counter()
                response = client.get(path)
            rows.append({'state': 'cold' if sample == 0 else 'warm',
                         'status': response.status_code,
                         'request_ms': round((time.perf_counter() - start) * 1000, 3),
                         'sql_count': len(sql_durations), 'sql_ms': round(sum(sql_durations), 3),
                         'sql_max_ms': round(max(sql_durations, default=0), 3),
                         'template_ms': round(sum(render_durations), 3),
                         'view_inclusive_ms': round(sum(view_durations), 3),
                         'response_bytes': len(response.content),
                         'peak_rss_mb': rss_peak_mb(),
                         'server_timing': response.get('Server-Timing', '')})
        warm = rows[1:]
        results['routes'][path] = {'cold': rows[0],
                                  'warm_medians': {key: statistics.median(row[key] for row in warm)
                                       for key in ('request_ms', 'sql_count', 'sql_ms', 'sql_max_ms', 'template_ms', 'view_inclusive_ms')},
                                  'samples': rows}
    transaction.set_rollback(True)
results['peak_rss_mb_final'] = rss_peak_mb()
Path(args.out).parent.mkdir(parents=True, exist_ok=True)
Path(args.out).write_text(json.dumps(results, indent=2))
print(json.dumps({path: item['warm_medians'] for path, item in results['routes'].items()}, indent=2))
print('Peak process RSS MB:', results['peak_rss_mb_final'])
