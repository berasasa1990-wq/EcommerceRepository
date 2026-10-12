from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Product, ProductWarehouseMeta


class WMSMobileStockFilterTests(TestCase):
    def test_minimum_filter_search_and_regular_results(self):
        user = get_user_model().objects.create_superuser('mobile-wms', 'wms@example.com', 'test-only')
        self.client.force_login(user)
        low = Product.objects.create(naziv='Štap niski', slug='stap-niski', cijena=10, stanje=3)
        regular = Product.objects.create(naziv='Štap normalni', slug='stap-normalni', cijena=10, stanje=12)
        ProductWarehouseMeta.objects.create(product=low, min_zaliha=5)
        ProductWarehouseMeta.objects.create(product=regular, min_zaliha=5)
        with patch('EcommerceApp.views_wms.module_locked', return_value=False), patch('EcommerceApp.views_wms.render', side_effect=lambda request, template, context: __import__('django.http', fromlist=['HttpResponse']).HttpResponse(','.join(str(p.pk) for p in context['products']))):
            response = self.client.get('/wms/', {'q':'Štap','stock_filter':'low'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content.decode(), str(low.pk))
            response = self.client.get('/wms/', {'q':'Štap'})
            self.assertEqual(set(response.content.decode().split(',')), {str(low.pk), str(regular.pk)})

    def test_name_typos_words_tags_and_identifiers(self):
        from django.http import HttpResponse
        from .models import Tag
        user = get_user_model().objects.create_superuser('search-wms', 'search@example.com', 'test-only')
        self.client.force_login(user)
        target = Product.objects.create(naziv='Shimano Ultegra štap', naziv_normalized='shimano ultegra stap', slug='search-shimano', sifra='SH5500', barkod='87170098', cijena=10, stanje=12)
        target.tagovi.add(Tag.objects.create(naziv='Premium feeder'))
        Product.objects.create(naziv='Daiwa mreža', slug='search-daiwa', cijena=10, stanje=4)
        with patch('EcommerceApp.views_wms.module_locked', return_value=False), patch('EcommerceApp.views_wms.render', side_effect=lambda request, template, context: HttpResponse(','.join(str(p.pk) for p in context['products']))):
            for query in ('Shimano', 'SH5500', '87170098', 'Premium feeder', 'Shomano', 'Shiamno', 'Ultegra Shimano', 'stap'):
                with self.subTest(query=query):
                    response = self.client.get('/wms/', {'q': query})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content.decode(), str(target.pk))
            response = self.client.get('/wms/', {'q': 'nepostojeci proizvod'})
            self.assertEqual(response.content.decode(), '')
