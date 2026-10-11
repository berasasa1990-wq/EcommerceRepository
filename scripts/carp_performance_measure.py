import django, json, logging, time, statistics, sys
from collections import Counter
from pathlib import Path
logging.disable(50)
import os
assert os.environ.get('DJANGO_SETTINGS_MODULE') in ('bera_wms_qa_settings', 'canonical_qa_settings')
django.setup()
from django.conf import settings
assert settings.DATABASES['default']['ENGINE']=='django.db.backends.sqlite3'
assert str(settings.DATABASES['default']['NAME']) in ('/private/tmp/bera-wms-qa.sqlite3', '/private/tmp/bera-wms-canonical-qa.sqlite3')
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.db import connection, transaction
from django.contrib.auth import get_user_model
from EcommerceApp.models import Product
results={}
with transaction.atomic():
 staff=Client();staff.force_login(get_user_model().objects.filter(is_superuser=True).first())
 public=Client()
 p=Product.objects.first()
 paths=[('/wms/',staff),('/wms/zalihe/?q=QA',staff),('/wms/narudzbe/',staff),('/wms/pakovanje/',staff),('/wms/lokacije/',staff),('/wms/kupci/',staff),('/wms/istorija/',staff),('/wms/narudzbe/artikli/?q=QA',staff),('/wms/narudzbe/kupci/?q=QA',staff),('/',public),('/?q=QA',public),('/artikal/'+p.slug+'/',public),('/korpa/',public),('/nalog/',staff),('/panel',staff),('/admin/',staff)]
 for path,client in paths:
  samples=[]
  for i in range(3):
   start=time.perf_counter()
   with CaptureQueriesContext(connection) as cap: response=client.get(path)
   samples.append({'ms':round((time.perf_counter()-start)*1000,2),'queries':len(cap),'status':response.status_code,'bytes':len(response.content),'schema_queries':sum('sqlite_master' in q['sql'] or 'PRAGMA' in q['sql'] for q in cap)})
  results[path]={'samples':samples,'median_ms':statistics.median(s['ms'] for s in samples),'queries_last':samples[-1]['queries'],'schema_queries_last':samples[-1]['schema_queries']}
 transaction.set_rollback(True)
Path(sys.argv[1]).write_text(json.dumps(results,indent=2))
print(json.dumps({k:{a:v[a] for a in ('median_ms','queries_last','schema_queries_last')} for k,v in results.items()},indent=2))
