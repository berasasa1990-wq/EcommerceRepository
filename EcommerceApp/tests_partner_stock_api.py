from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Product, ProductVariation, WarehouseLocation, WarehouseStock


@override_settings(PARTNER_STOCK_API_KEY='partner-only-token', SYNC_API_KEY='internal-token',
                   CATALOG_SYNC_API_KEY='catalog-token', ALLOWED_HOSTS=['testserver'], SITE_PREP_ENABLED=False)
class PartnerStockAPITests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Varalica', sifra='VAR-1', cijena=123,
                                              aktivan=True, magacin_sync_at=timezone.now())
        self.variant = ProductVariation.objects.create(artikal=self.product, naziv='Crvena', sifra='RED')
        self.location = WarehouseLocation.objects.create(sifra='A-01', naziv='Polica')
        WarehouseStock.objects.create(product=self.product, variation=self.variant, location=self.location,
                                      kolicina=7, rezervisano=3)
        self.url = reverse('partner_stock_products')
        self.headers = {'HTTP_AUTHORIZATION': 'Bearer partner-only-token'}

    def test_allowlisted_fields_and_available_stock(self):
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, 200)
        row = response.json()['results'][0]
        self.assertEqual(set(row), {'id', 'name', 'sku', 'quantity', 'variants'})
        self.assertEqual(row['quantity'], 4)
        self.assertEqual(row['variants'], [{'id': self.variant.pk, 'name': 'Crvena', 'sku': 'RED', 'quantity': 4}])
        self.assertIn('no-store', response['Cache-Control'])

    def test_authentication_and_no_query_string_token(self):
        for headers in ({}, {'HTTP_AUTHORIZATION': 'Bearer wrong'}, {'HTTP_AUTHORIZATION': 'Bearer internal-token'}, {'HTTP_X_API_KEY': 'partner-only-token'}):
            self.assertEqual(self.client.get(self.url, **headers).status_code, 401)
        self.assertEqual(self.client.get(self.url, {'api_key': 'partner-only-token'}).status_code, 401)
        with override_settings(PARTNER_STOCK_API_KEY=''):
            self.assertEqual(self.client.get(self.url, **self.headers).status_code, 503)
        with override_settings(PARTNER_STOCK_API_KEY='internal-token'):
            self.assertEqual(self.client.get(self.url, HTTP_AUTHORIZATION='Bearer internal-token').status_code, 503)

    def test_partner_token_cannot_access_existing_api(self):
        self.assertEqual(self.client.get(reverse('catalog_api_products'), **self.headers).status_code, 401)
        self.assertEqual(self.client.get(reverse('catalog_api_magacin_stanje'), **self.headers).status_code, 401)

    def test_write_methods_are_denied(self):
        for url in (self.url, reverse('partner_stock_product', args=[self.product.pk])):
            for method in ('post', 'put', 'patch', 'delete'):
                self.assertEqual(getattr(self.client, method)(url, {}, content_type='application/json', **self.headers).status_code, 405)
        self.assertEqual(WarehouseStock.objects.get().kolicina, 7)

    def test_pagination_visibility_detail_and_parameters(self):
        other = Product.objects.create(naziv='Drugi', cijena=10, aktivan=True)
        hidden = Product.objects.create(naziv='Skriven', cijena=10, sakriven_do_stanja=True)
        inactive = Product.objects.create(naziv='Neaktivan', cijena=10, aktivan=False)
        response = self.client.get(self.url, {'page_size': 1}, **self.headers).json()
        self.assertEqual((response['count'], response['next_page']), (2, 2))
        self.assertEqual(self.client.get(self.url, {'page_size': 1, 'page': 2}, **self.headers).json()['results'][0]['id'], other.pk)
        for params in ({'page': 'oops'}, {'page': 0}, {'page_size': 101}):
            self.assertEqual(self.client.get(self.url, params, **self.headers).status_code, 400)
        for pk in (hidden.pk, inactive.pk, 99999):
            self.assertEqual(self.client.get(reverse('partner_stock_product', args=[pk]), **self.headers).status_code, 404)
        detail = self.client.get(reverse('partner_stock_product', args=[self.product.pk]), **self.headers)
        self.assertEqual(detail.json()['quantity'], 4)
        self.assertEqual(self.client.get(self.url, {'sku': 'VAR-1'}, **self.headers).json()['count'], 1)

    def test_zero_stock_and_virtual_transfer_exclusion(self):
        transfer = WarehouseLocation.objects.create(sifra='Prenos u MP', naziv='Prenos u MP')
        WarehouseStock.objects.create(product=self.product, location=transfer, kolicina=100)
        WarehouseStock.objects.filter(location=self.location).update(rezervisano=7)
        self.assertEqual(self.client.get(self.url, **self.headers).json()['results'][0]['quantity'], 0)

    @override_settings(SITE_PREP_ENABLED=True, SITE_PREP_PASSWORD='site-password')
    def test_own_auth_works_during_site_preparation(self):
        self.assertEqual(self.client.get(self.url, **self.headers).status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 401)
