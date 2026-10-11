"""Additional canonical stock tests; synthetic clone only, rollback all writes."""
import importlib, logging
from unittest.mock import patch
from django.conf import settings
from django.apps import apps
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model
from EcommerceApp.models import Product, WarehouseStock, WarehouseLocation, WMSLocation, WMSPickLine, WMSOrder, WMSOrderItem, ProductWMSStock, ProductVariation, WMSTransfer
assert str(settings.DATABASES['default']['NAME']) == '/private/tmp/bera-wms-canonical-qa.sqlite3'
logging.disable(50)
with transaction.atomic():
 client=Client();client.force_login(get_user_model().objects.filter(is_superuser=True).first())
 product=Product.objects.first()
 location=WarehouseLocation.objects.create(sifra='QA-DIRECT',naziv='QA original location')
 original=WarehouseStock.objects.create(product=product,location=location,kolicina=7)
 mirror=list(ProductWMSStock.objects.values_list('pk','kolicina'))
 with patch('EcommerceApp.wms_legacy.import_legacy_stock') as sync, patch('EcommerceApp.wms_legacy.apply_wms_stock') as copied_write:
  with CaptureQueriesContext(connection) as sql:
   response=client.get('/wms/zalihe/',{'q':product.sifra})
  assert response.status_code==200
  assert 'QA original location' in response.content.decode()
  assert all('EcommerceApp_bera_productwmsstock' not in row['sql'] for row in sql)
  sync.assert_not_called();copied_write.assert_not_called()
  WarehouseStock.objects.filter(pk=original.pk).update(kolicina=9)
  response=client.get(f'/wms/lokacije/{location.pk}/')
  assert '9 kom' in response.content.decode()
 assert list(ProductWMSStock.objects.values_list('pk','kolicina'))==mirror
 # Canonical stock row retains its variant during transfers.
 variant=ProductVariation.objects.first()
 if variant:
  dest=WarehouseLocation.objects.create(sifra='QA-VAR-DEST',naziv='QA variant target')
  WarehouseStock.objects.create(product=variant.artikal,variation=variant,location=location,kolicina=4)
  transfer=WMSTransfer.objects.create(product=variant.artikal,variation=variant,source=location,destination=dest,quantity=2)
  assert client.post('/wms/prenosnica/',{'transfer_id':transfer.pk}).status_code==302
  transfer.refresh_from_db();assert transfer.completed_at
  assert WarehouseStock.objects.get(product=variant.artikal,variation=variant,location=dest).kolicina==2
 # Backfill maps location identity, never copies or edits stock.
 historical=WMSLocation.objects.create(naziv='QA archived',legacy_location=location)
 order=WMSOrder.objects.create(tip='vp',kupac='QA history')
 item=WMSOrderItem.objects.create(order=order,artikal=product,naziv=product.naziv,cijena=10,bazna_cijena=10,kolicina=1)
 line=WMSPickLine.objects.create(order=order,wms_item=item,archived_location=historical,quantity=1)
 migration=importlib.import_module('EcommerceApp.migrations.0315_wms_canonical_warehouse')
 before=list(WarehouseStock.objects.values_list('pk','kolicina','rezervisano'))
 class Editor:
  connection=connection
 migration.map_locations(apps,Editor())
 line.refresh_from_db();assert line.location_id==location.pk and line.archived_location_id==historical.pk
 assert before==list(WarehouseStock.objects.values_list('pk','kolicina','rezervisano'))
 transaction.set_rollback(True)
print('PASS: no mirror reads/writes or sync; live original stock; variant transfer; historical mapping; original quantities unchanged by backfill')
