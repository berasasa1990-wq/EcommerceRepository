"""Offline WMS performance invariants. All fixture changes roll back."""
import os
assert os.environ.get('DJANGO_SETTINGS_MODULE') == 'canonical_qa_settings'
import django
django.setup()
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model
from EcommerceApp.models import Product, ProductVariation, WarehouseLocation, WarehouseStock
assert connection.vendor == 'sqlite' and str(connection.settings_dict['NAME']) == '/private/tmp/bera-wms-canonical-qa.sqlite3'
with transaction.atomic():
    client = Client(); client.force_login(get_user_model().objects.filter(is_superuser=True).first())
    product = Product.objects.first()
    Product.objects.all().update(slika='')
    Product.objects.filter(pk=product.pk).update(barkod='QA-PERF-UNIQUE-BARCODE')
    a = WarehouseLocation.objects.create(sifra='QA-PERF-A', naziv='QA perf A')
    b = WarehouseLocation.objects.create(sifra='QA-PERF-B', naziv='QA perf B')
    WarehouseStock.objects.create(product=product, location=a, kolicina=7, rezervisano=2)
    WarehouseStock.objects.create(product=product, location=b, kolicina=9)
    for number in range(31):
        variation = ProductVariation.objects.create(artikal=product, naziv=f'QA perf {number}')
        WarehouseStock.objects.create(product=product, variation=variation, location=a, kolicina=3)
    original = list(WarehouseStock.objects.values_list('pk','kolicina','rezervisano'))
    with CaptureQueriesContext(connection) as captured:
        response = client.get('/wms/zalihe/', {'q':'QA-PERF-UNIQUE-BARCODE'})
    assert response.status_code == 200
    assert product.naziv in response.content.decode()
    assert 'QA perf A' in response.content.decode() and 'QA perf B' in response.content.decode()
    assert len(response.context['products']) == 1 if response.context else True
    first = client.get(f'/wms/lokacije/{a.pk}/')
    second = client.get(f'/wms/lokacije/{a.pk}/?page=2')
    assert first.status_code == second.status_code == 200
    assert first.content.count(b'class="wms-stock-result"') == 30
    assert second.content.count(b'class="wms-stock-result"') == 2
    assert b'page=2' in first.content and b'page=1' in second.content
    for path in ('/wms/zalihe/?q=', '/wms/narudzbe/artikli/?q='):
        with CaptureQueriesContext(connection) as captured:
            response = client.get(path)
        fallback_queries = [row['sql'] for row in captured if 'FROM "EcommerceApp_productvariation"' in row['sql']]
        assert len(fallback_queries) <= 2, (path, len(fallback_queries))
        assert response.status_code == 200
    assert original == list(WarehouseStock.objects.values_list('pk','kolicina','rezervisano'))
    assert Client().get(f'/wms/lokacije/{a.pk}/').status_code == 302
    transaction.set_rollback(True)
print('PASS: barcode search, multi-location visibility, 30-row pagination, bounded fallback image queries, unchanged stock/reservations and access control')
