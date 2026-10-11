"""Offline, rollback-only WMS profiling on the synthetic SQLite fixture."""
import os, json, time, cProfile, pstats, sys
from html.parser import HTMLParser
from pathlib import Path
assert os.environ.get('DJANGO_SETTINGS_MODULE') == 'canonical_qa_settings'
import django
django.setup()
from django.conf import settings
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model
from EcommerceApp.models import Product, WarehouseLocation, WarehouseStock
assert connection.vendor == 'sqlite'
assert str(settings.DATABASES['default']['NAME']) == '/private/tmp/bera-wms-canonical-qa.sqlite3'
class Elements(HTMLParser):
    count = 0
    def handle_starttag(self, tag, attrs): self.count += 1
    def handle_startendtag(self, tag, attrs): self.count += 1
result = {}
with transaction.atomic():
    # Exercise fallback images: the prior fixture's main images hide N+1 queries.
    Product.objects.all().update(slika='')
    location = WarehouseLocation.objects.create(sifra='QA-PROFILE', naziv='QA profiler')
    for product in Product.objects.all()[:70]:
        WarehouseStock.objects.create(product=product, location=location, kolicina=5)
    client = Client(); client.force_login(get_user_model().objects.filter(is_superuser=True).first())
    paths = ['/wms/', '/wms/zalihe/?q=', '/wms/narudzbe/artikli/?q=', '/wms/lokacije/', f'/wms/lokacije/{location.pk}/', '/wms/narudzbe/', '/wms/istorija/']
    for path in paths:
        client.get(path)
        sql_durations = []
        def timed_sql(execute, sql, params, many, context):
            started = time.perf_counter()
            try: return execute(sql, params, many, context)
            finally: sql_durations.append((time.perf_counter()-started)*1000)
        profile = cProfile.Profile(); start = time.perf_counter()
        with connection.execute_wrapper(timed_sql), CaptureQueriesContext(connection) as queries:
            profile.enable(); response = client.get(path); profile.disable()
        elapsed = (time.perf_counter()-start)*1000
        parser = Elements(); parser.feed(response.content.decode())
        stats = pstats.Stats(profile)
        view = [{ 'function':key[2], 'total_ms':round(value[3]*1000,3)} for key,value in stats.stats.items() if key[0].endswith('/views_wms.py') and key[2] in ('workspace','order_products','location_detail')]
        result[path] = {'status':response.status_code,'server_ms_with_profiler':round(elapsed,3),'sql_count':len(queries),'sql_ms':round(sum(sql_durations),3),'bytes':len(response.content),'html_start_tags':parser.count if 'text/html' in response.get('Content-Type','') else None,'view_inclusive_ms':view,'sql':list(queries)}
        assert response.status_code == 200
    result['fixture'] = {'products':Product.objects.count(),'location_rows':WarehouseStock.objects.filter(location=location).count()}
    transaction.set_rollback(True)
Path(sys.argv[1]).write_text(json.dumps(result,indent=2))
print(json.dumps({k:{f:v for f,v in val.items() if f!='sql'} for k,val in result.items()},indent=2))
