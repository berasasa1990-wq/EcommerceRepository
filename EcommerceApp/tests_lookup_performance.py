from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import Product, ProductVariation, Akcija, AkcijaFlashLine


class LookupPerformanceTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('perf', 'perf@example.com', 'test'))
        offer = Akcija.objects.create(naziv='Popust', tip='akcijska', popust_postotak=10)
        for i in range(8):
            product = Product.objects.create(naziv=f'Brzina {i}', cijena=100, stanje=10, na_stanju=True, magacin_sync_at=timezone.now())
            AkcijaFlashLine.objects.create(akcija=offer, product=product)
            for j in range(2):
                ProductVariation.objects.create(artikal=product, naziv=f'Var {j}', cijena=120, stanje=5, na_stanju=True)
        self.url = reverse('staff_magacin_artikli_lookup')

    def measure(self, limit):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {'q': 'Brzina', 'bez_zalihe': '1', 'limit': limit})
        self.assertEqual(response.status_code, 200)
        return len(queries), response.json()['results']

    def test_queries_do_not_grow_per_product_or_variation(self):
        self.measure(1)
        small, _ = self.measure(1)
        large, rows = self.measure(8)
        print(f'Lookup queries: 1 product={small}, 8 products={large}')
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]['cijena'], '90.00')
        self.assertEqual(rows[0]['varijacije'][0]['cijena'], '108.00')
        self.assertLessEqual(large, small + 1)

    def test_storefront_suggestions_batch_prices_and_variations(self):
        self.url = reverse('search_suggest')
        self.measure(1)
        small, _ = self.measure(1)
        large, rows = self.measure(8)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row['on_sale'] for row in rows))
        self.assertLessEqual(large, small + 1)
