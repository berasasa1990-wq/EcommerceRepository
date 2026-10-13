from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .magacin import display_stock_totals, display_variant_stock_totals
from .models import Order, Product, ProductVariation, WarehouseLocation, WarehouseStock
from .views_magacin import _magacin_nav_counts, collect_pick_jobs, magacin_artikli


class WarehousePerformanceTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Performance item', sifra='PERF-1', cijena=Decimal('10'), magacin_sync_at=timezone.now())
        self.location = WarehouseLocation.objects.create(sifra='PERF-A', naziv='Magacin')
        self.mp = WarehouseLocation.objects.create(sifra='PERF-MP', naziv='Maloprodaja')
        self.variants = [ProductVariation.objects.create(artikal=self.product, naziv='Var %s' % i) for i in range(20)]

    def test_batched_totals_match_existing_rules_with_two_queries(self):
        WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=8, rezervisano=2)
        WarehouseStock.objects.create(product=self.product, location=self.mp, kolicina=5, rezervisano=1)
        WarehouseStock.objects.create(product=self.product, variation=self.variants[0], location=self.location, kolicina=3, rezervisano=5)
        WarehouseStock.objects.create(product=self.product, variation=self.variants[0], location=self.mp, kolicina=2, rezervisano=1)
        WarehouseStock.objects.create(product=self.product, variation=self.variants[1], location=self.location, kolicina=-2, rezervisano=-1)
        WarehouseStock.objects.create(product=self.product, variation=self.variants[1], location=self.mp, kolicina=0, rezervisano=0)
        ignored = WarehouseLocation.objects.create(sifra='PERF-TRANS', naziv='Prenos u MP')
        WarehouseStock.objects.create(product=self.product, location=ignored, kolicina=100)
        inactive = WarehouseLocation.objects.create(sifra='PERF-OLD', naziv='Maloprodaja stara', aktivan=False)
        WarehouseStock.objects.create(product=self.product, location=inactive, kolicina=100)
        with CaptureQueriesContext(connection) as previous_queries:
            expected = {var.pk: display_stock_totals(self.product, var) for var in self.variants}
        self.assertGreater(len(previous_queries), 40)
        with self.assertNumQueries(2):
            actual = display_variant_stock_totals(self.product, self.variants)
        self.assertEqual(actual, expected)

    def test_no_variants_needs_no_queries_and_negative_stock_preserved(self):
        with self.assertNumQueries(0):
            self.assertEqual(display_variant_stock_totals(self.product, []), {})
        WarehouseStock.objects.create(product=self.product, variation=self.variants[0], location=self.location, kolicina=-4, rezervisano=-3)
        expected = display_stock_totals(self.product, self.variants[0])
        self.assertEqual(display_variant_stock_totals(self.product, self.variants)[self.variants[0].pk], expected)

    def test_nav_only_counts_orders_instead_of_loading_them(self):
        Order.objects.create(ime_prezime='Performance order', ukupno=10)
        with CaptureQueriesContext(connection) as queries:
            counts = _magacin_nav_counts()
        self.assertEqual(counts['new_magacin_orders_count'], 1)
        self.assertEqual(counts['new_pack_orders_count'], 1)
        order_queries = [q['sql'] for q in queries if 'FROM "EcommerceApp_order"' in q['sql']]
        self.assertTrue(order_queries)
        self.assertTrue(all('COUNT(' in sql for sql in order_queries))
        self.assertFalse(any('FROM "EcommerceApp_orderitem"' in q['sql'] for q in queries))

    def test_pick_list_counts_items_without_prefetching_full_items(self):
        from .models import OrderItem
        order = Order.objects.create(ime_prezime='Performance pick', ukupno=10)
        OrderItem.objects.create(narudzba=order, naziv='Line', cijena=10, kolicina=1)
        with CaptureQueriesContext(connection) as queries:
            jobs = collect_pick_jobs()
        self.assertEqual(jobs[0].stavki, 1)
        self.assertEqual(jobs[0].pick_status, 'ceka')
        self.assertFalse(any('FROM "EcommerceApp_orderitem"' in q['sql'] for q in queries))

    def test_search_evaluates_only_current_page(self):
        for i in range(65):
            Product.objects.create(naziv='Search benchmark %03d' % i, cijena=10, magacin_sync_at=timezone.now())
        req = RequestFactory().get('/nalog/magacin/artikli/', {'pretraga': 'Search benchmark', 'rezultati': '1'})
        req.user = get_user_model().objects.create_superuser('perf', 'perf@example.com', 'test')
        req.session = {}
        with patch('EcommerceApp.views_magacin._magacin_context', return_value={}), patch('EcommerceApp.views_magacin.render', side_effect=lambda r, t, c: c):
            with CaptureQueriesContext(connection) as queries:
                context = magacin_artikli(req)
        self.assertEqual(context['result_count'], 65)
        self.assertEqual(len(context['page'].object_list), 40)
        self.assertTrue(context['include_zero'])
        product_reads = [q['sql'] for q in queries if 'FROM "EcommerceApp_product"' in q['sql'] and 'COUNT(' not in q['sql']]
        self.assertTrue(all('LIMIT ' in sql for sql in product_reads))
