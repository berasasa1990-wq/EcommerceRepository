"""Rollback-only profiling of the isolated product changelist."""
import os,json,time,sys
from pathlib import Path
assert os.environ.get('DJANGO_SETTINGS_MODULE')=='canonical_qa_settings'
import django
django.setup()
from django.db import transaction,connection
from django.test import Client
from django.contrib.auth import get_user_model
from django.test.utils import CaptureQueriesContext
assert connection.vendor=='sqlite' and str(connection.settings_dict['NAME'])=='/private/tmp/bera-wms-canonical-qa.sqlite3'
with transaction.atomic():
 c=Client();c.force_login(get_user_model().objects.filter(is_superuser=True).first())
 url='/panel/podesavanja/sekcije/EcommerceApp/product/'
 c.get(url)
 rows=[]
 for query in ('','?q=QA','?ima_varijacije=da','?ima_varijacije=ne'):
  start=time.perf_counter()
  with CaptureQueriesContext(connection) as cap:r=c.get(url+query)
  assert r.status_code==200,(r.status_code,url)
  rows.append({'url':url+query,'ms':round((time.perf_counter()-start)*1000,2),'queries':len(cap),'bytes':len(r.content),'sql':list(cap)})
 transaction.set_rollback(True)
Path(sys.argv[1]).write_text(json.dumps(rows,indent=2)); print(json.dumps([{k:v for k,v in row.items() if k!='sql'} for row in rows],indent=2))
