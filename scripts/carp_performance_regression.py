"""Run with bera_wms_qa_settings only; all fixture changes are rolled back."""
import logging
from unittest.mock import patch
from django.conf import settings
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.db import connection, transaction
from django.contrib.auth import get_user_model
from EcommerceApp.models import ModulePermissions, Product, WMSOrder
from EcommerceApp.request_performance import RequestReadMiddleware, request_read
from EcommerceApp.bera_panel_modules import module_locked

assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
assert str(settings.DATABASES['default']['NAME']) == '/private/tmp/bera-wms-qa.sqlite3'
logging.disable(50)
with transaction.atomic():
    modules, _ = ModulePermissions.objects.get_or_create(pk=1)
    modules.wms_zalihe = True
    modules.save()
    def read_flags(request):
        with CaptureQueriesContext(connection) as queries:
            assert module_locked('wms_zalihe') is False
            assert module_locked('wms_zalihe') is False
            module_locked('wms_narudzbe')
        assert len(queries) == 2, len(queries)  # schema and singleton
    middleware = RequestReadMiddleware(read_flags)
    middleware(RequestFactory().get('/'))
    modules.wms_zalihe = False
    modules.save()
    RequestReadMiddleware(lambda request: (_ for _ in ()).throw(AssertionError()) if not module_locked('wms_zalihe') else None)(RequestFactory().get('/'))
    modules.wms_zalihe = True
    modules.save()
    # POST reads must see changes in the same request.
    def post_flags(request):
        assert module_locked('wms_zalihe') is False
        ModulePermissions.objects.filter(pk=1).update(wms_zalihe=False)
        assert module_locked('wms_zalihe') is True
    RequestReadMiddleware(post_flags)(RequestFactory().post('/'))
    modules.save()
    client = Client()
    client.force_login(get_user_model().objects.filter(is_superuser=True).first())
    with patch('EcommerceApp.wms_legacy.import_legacy_stock') as sync:
        response = client.get('/wms/zalihe/', HTTP_X_WMS_PERMISSIONS='1')
        assert response.status_code == 200
        assert response.json() == {'redirect': None}
        sync.assert_not_called()
    product = Product.objects.first()
    expected = product.prikazna_cijena
    def prices(request):
        with CaptureQueriesContext(connection) as queries:
            first = Product.objects.get(pk=product.pk)
            assert first.prikazna_cijena == expected
            assert first.prikazna_cijena == expected
            assert first.na_akciji == product.na_akciji
        flash_queries = [q for q in queries if 'FROM "EcommerceApp_akcijaflashline"' in q['sql']]
        assert len(flash_queries) == 1, len(flash_queries)
    RequestReadMiddleware(prices)(RequestFactory().get('/'))
    for i in range(51):
        WMSOrder.objects.create(tip='vp', kupac=f'QA Perf {i}', status='nova')
    response = client.get('/wms/narudzbe/', {'page': 1})
    assert response.status_code == 200
    assert 'Sljedeća' in response.content.decode()
    response = client.get('/wms/narudzbe/', {'page': 2})
    assert response.status_code == 200
    assert 'Prethodna' in response.content.decode()
    assert Client().get('/wms/narudzbe/').status_code == 302
    transaction.set_rollback(True)
print('PASS: request cache scope, permission freshness, POST reads, permission poll skips stock sync, unchanged prices, bounded pagination and access')
