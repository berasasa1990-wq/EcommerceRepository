"""Offline SQL directory parity and profiling; fixture writes roll back."""
import os,json,time,tracemalloc
from pathlib import Path
assert os.environ.get('DJANGO_SETTINGS_MODULE')=='canonical_qa_settings'
import django
django.setup()
from django.db import connection,transaction
from django.db.models import Q
from django.core.paginator import Paginator
from django.test.utils import CaptureQueriesContext
from EcommerceApp.models import WMSCustomer,Order,WMSOrder
from EcommerceApp.wms_customer_query import customer_page
assert connection.vendor=='sqlite' and str(connection.settings_dict['NAME'])=='/private/tmp/bera-wms-canonical-qa.sqlite3'
def original(query,number):
 c=WMSCustomer.objects.filter(is_deleted=False);h=Order.objects.all();m=WMSOrder.objects.filter(source_order__isnull=True,customer__isnull=True)
 if query:
  c=c.filter(Q(ime_prezime__icontains=query)|Q(telefon__icontains=query)|Q(grad__icontains=query));h=h.filter(Q(ime_prezime__icontains=query)|Q(telefon__icontains=query)|Q(grad__icontains=query));m=m.filter(Q(kupac__icontains=query)|Q(telefon__icontains=query)|Q(grad__icontains=query))
 rows=[dict(id=x.pk,name=x.ime_prezime,phone=x.telefon,address=x.adresa,city=x.grad,postal=x.postanski_broj) for x in c]
 rows += [dict(id='',name=x.ime_prezime,phone=x.telefon,address=x.adresa,city=x.grad,postal=x.postanski_broj,email=x.email) for x in h.order_by('-kreirana')]
 rows += [dict(id='',name=x.kupac,phone=x.telefon,address=x.adresa,city=x.grad,postal=x.postanski_broj,email=x.email) for x in m.order_by('-kreirana')]
 deleted={(x.ime_prezime.strip().lower(),x.telefon.strip()) for x in WMSCustomer.objects.filter(is_deleted=True)}
 unique={}
 for row in rows:
  key=(row['name'].strip().lower(),row['phone'].strip())
  if key not in deleted:unique.setdefault(key,row)
 page=Paginator(sorted(unique.values(),key=lambda x:x['name'].lower()),30).get_page(number)
 return dict(customers=list(page),page=page.number,pages=page.paginator.num_pages)
results=[]
with transaction.atomic():
 WMSCustomer.objects.bulk_create([WMSCustomer(ime_prezime=f'QA Customer {i:04d}',telefon=f'061{i:06d}',adresa='QA',grad='QA City',postanski_broj='71000') for i in range(1200)])
 WMSCustomer.objects.create(ime_prezime='QA Kupac',telefon='061000001',adresa='Priority WMS',grad='QA',postanski_broj='71000')
 WMSCustomer.objects.create(ime_prezime=' qa kupac ',telefon=' 061000001 ',adresa='hidden',grad='QA',postanski_broj='71000',is_deleted=True)
 WMSCustomer.objects.create(ime_prezime='Željko QA',telefon='067000001',adresa='QA',grad='QA',postanski_broj='71000')
 WMSCustomer.objects.create(ime_prezime='Đorđe QA',telefon='067000002',adresa='QA',grad='QA',postanski_broj='71000')
 for query in ('','QA Customer','061000001','QA City','QA'):
  for number in (1,2,9999,'bad',0):
   assert customer_page(query,number)==original(query,number),(query,number)
 for label,func in [('before',original),('after',customer_page)]:
  tracemalloc.start()
  start=time.perf_counter()
  with CaptureQueriesContext(connection) as cap: result=func('',2)
  peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
  results.append(dict(peak_python_bytes=peak,stage=label,ms=round((time.perf_counter()-start)*1000,3),queries=len(cap),bytes=len(json.dumps(result).encode()),rows_returned=len(result['customers'])))
 transaction.set_rollback(True)
Path('reports/performance/wms-customers-measurements.json').write_text(json.dumps(results,indent=2))
print('PASS: directory parity, source priority, soft-deleted exclusion, filters, Unicode names, pages and invalid pages')
print(json.dumps(results))
