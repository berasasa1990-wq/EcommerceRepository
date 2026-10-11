import json,logging
from pathlib import Path
from django.test import Client
from django.contrib.auth import get_user_model
from django.db import transaction
from django.conf import settings
from EcommerceApp.models import Product,WarehouseLocation,WarehouseStock,ProductWMSStock,WMSLocation,WMSSettings,WMSTransfer,WMSOrder,WMSOrderItem,Order
logging.disable(50)
assert str(settings.DATABASES['default']['NAME']) == '/private/tmp/bera-wms-qa.sqlite3'
r={}
with transaction.atomic():
 c=Client();c.force_login(get_user_model().objects.filter(is_superuser=True).first())
 p=Product.objects.get(pk=1)
 WarehouseStock.objects.filter(product=p).delete();ProductWMSStock.objects.filter(product=p).delete()
 a=WarehouseLocation.objects.create(sifra='QA-WMS-A',naziv='QA Shelf A');b=WarehouseLocation.objects.create(sifra='QA-WMS-B',naziv='QA Shelf B')
 WarehouseStock.objects.create(product=p,location=a,kolicina=10)
 assert c.get('/wms/zalihe/',{'q':'QA'}).status_code==200
 wa=WMSLocation.objects.get(legacy_location=a);wb=WMSLocation.objects.create(naziv=b.sifra,legacy_location=b)
 stock=ProductWMSStock.objects.get(product=p,lokacija=wa);assert stock.kolicina==10
 settings,_=WMSSettings.objects.get_or_create(pk=1);settings.transfer_location=wb;settings.save()
 r['legacy_stock_visible']=True
 response=c.post('/wms/zalihe/',{'product_id':p.pk,'stock_id':stock.pk,'source_location':wa.pk,'quantity':2,'q':'QA'})
 assert response.status_code==302
 transfer=WMSTransfer.objects.latest('pk')
 assert not transfer.completed_at
 assert c.post('/wms/prenosnica/',{'transfer_id':transfer.pk}).status_code==302
 assert WarehouseStock.objects.get(product=p,location=a,variation_key=0).kolicina==8
 assert WarehouseStock.objects.get(product=p,location=b,variation_key=0).kolicina==2
 assert c.post('/wms/prenosnica/',{'transfer_id':transfer.pk}).status_code==302
 assert WarehouseStock.objects.get(product=p,location=a,variation_key=0).kolicina==8
 r['transfer_and_no_double_transfer']=True
 response=c.post('/wms/narudzbe/kreiraj/vp/',{'kupac':'QA WMS Buyer','telefon':'061123456','adresa':'Test','grad':'Sarajevo','postanski_broj':'71000','items':json.dumps([{'id':p.pk,'quantity':2,'discount':0}])})
 assert response.status_code==302
 order=WMSOrder.objects.latest('pk');assert order.items.count()==1
 assert c.post(f'/wms/narudzbe/{order.pk}/odvajanje/',{'action':'start'}).status_code==302
 order.refresh_from_db();assert order.status=='pakovanje'
 for line in order.pick_lines.all():
  assert c.post(f'/wms/narudzbe/{order.pk}/odvajanje/',{'action':'confirm','line_id':line.pk,'quantity':line.quantity}).status_code==302
 assert c.post(f'/wms/narudzbe/{order.pk}/odvajanje/',{'action':'finish'}).status_code==302
 order.refresh_from_db();assert order.status=='zapakovana'
 assert sum(WarehouseStock.objects.filter(product=p).values_list('kolicina',flat=True))==8
 r['manual_order_picking_and_legacy_stock']=True
 assert c.get(f'/wms/narudzbe/pregled/{order.pk}/').status_code==200
 assert c.get('/wms/narudzbe/stampa/faktura/',{'stage':'print','orders':[order.pk]}).status_code==200
 assert c.get('/wms/narudzbe/stampa/racun/',{'stage':'print','orders':[order.pk]}).status_code==200
 assert c.get('/wms/narudzbe/stampa/najava/').status_code==200
 assert c.get(f'/wms/lokacije/{wa.pk}/').status_code==200
 assert c.get(f'/wms/lokacije/{wa.pk}/barkod/').status_code==200
 r['order_detail_invoices_announcement_location_barcode']=True
 p.refresh_from_db()
 c.post('/artikal/'+p.slug+'/dodaj/',{'quantity':2})
 response=c.post('/narudzba/',{'ime_prezime':'QA Web Buyer','email':'qa-wms@example.com','telefon':'061234567','adresa':'QA address','grad':'Sarajevo','postanski_broj':'71000','payment_method':'cod'})
 assert response.status_code==302
 webshop=Order.objects.latest('pk')
 c.get('/wms/narudzbe/')
 web_wms=WMSOrder.objects.get(source_order=webshop)
 c.post(f'/wms/narudzbe/{web_wms.pk}/odvajanje/',{'action':'start'})
 for line in web_wms.pick_lines.all():
  c.post(f'/wms/narudzbe/{web_wms.pk}/odvajanje/',{'action':'confirm','line_id':line.pk,'quantity':line.quantity})
 c.post(f'/wms/narudzbe/{web_wms.pk}/odvajanje/',{'action':'finish'})
 webshop.refresh_from_db()
 assert webshop.stanje_skinuto and webshop.lager_status==Order.LagerStatus.VALIDIRANO, webshop.lager_status
 assert sum(WarehouseStock.objects.filter(product=p).values_list('kolicina',flat=True))==6
 c.post(f'/wms/narudzbe/{web_wms.pk}/odvajanje/',{'action':'finish'})
 assert sum(WarehouseStock.objects.filter(product=p).values_list('kolicina',flat=True))==6
 r['webshop_fulfillment_without_double_deduction']=True
 original=WarehouseStock.objects.get(product=p,location=a,variation_key=0)
 original.rezervisano=original.kolicina;original.save(update_fields=['rezervisano'])
 c.get('/wms/zalihe/',{'q':'QA'})
 blocked=WMSTransfer.objects.create(product=p,source=wa,destination=wb,quantity=1)
 c.post('/wms/prenosnica/',{'transfer_id':blocked.pk})
 blocked.refresh_from_db();assert blocked.completed_at is None
 current=WarehouseStock.objects.get(pk=original.pk)
 assert current.kolicina==original.kolicina and current.rezervisano==original.rezervisano
 r['reserved_stock_transfer_blocked']=True
 for section in ['zalihe','narudzbe','pakovanje','prenosnica','lokacije','kupci','podesavanje']:
  assert c.get('/wms/'+section+'/').status_code==200
 assert c.get('/wms/podesavanje/excel-import/',{'template':'1'}).status_code==200
 assert c.get('/nalog/magacin/artikli/').status_code==200
 r['sections_excel_template_legacy_magacin']=True
 normal=get_user_model().objects.create_user(username='wms-qa-plain');c.force_login(normal)
 assert c.get('/wms/').status_code==302
 assert c.post('/wms/prenosnica/',{'transfer_id':transfer.pk}).status_code==302
 r['permissions']=True
 transaction.set_rollback(True)
Path('reports/bera-wms-visual').mkdir(exist_ok=True)
Path('reports/bera-wms-visual/regression.json').write_text(json.dumps(r,indent=2))
print(json.dumps(r))
