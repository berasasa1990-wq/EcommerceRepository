import time
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import AnonymousUser
from django.db.models.signals import post_init
from django.db import connection
from django.test.utils import CaptureQueriesContext
from .models import Product
from .views import _home_product_queryset
from .bera_home import _selected_bestseller_products


class BestsellerPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Product.objects.bulk_create([Product(naziv=f'Perf {i}', slug=f'perf-{i}', opis='Opis ' * 4000,
            cijena=10, na_stanju=True, stanje=5) for i in range(80)])

    def setUp(self):
        self.request = RequestFactory().get('/')
        self.request.user = AnonymousUser()
        self.request.session = {}

    def test_only_six_full_products_are_loaded_with_same_selection(self):
        ids = list(Product.objects.order_by('-pk').values_list('pk', flat=True))
        def measure(callback):
            hydrated = []
            def initialized(sender, instance, **kwargs):
                hydrated.append(instance.pk)
            post_init.connect(initialized, sender=Product)
            try:
                start = time.perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    result = callback()
                return result, len(hydrated), len(queries), (time.perf_counter() - start) * 1000
            finally:
                post_init.disconnect(initialized, sender=Product)
        def baseline():
            cards = {p.pk: p for p in _home_product_queryset(self.request).filter(pk__in=ids)}
            return [cards[pk] for pk in ids if pk in cards][:6]
        before, before_models, before_sql, before_ms = measure(baseline)
        after, after_models, after_sql, after_ms = measure(lambda: _selected_bestseller_products(ids, self.request))
        self.assertEqual([p.pk for p in before], [p.pk for p in after])
        self.assertEqual(before_models, 80)
        self.assertEqual(after_models, 6)
        print(f'Bestseller before: {before_models} full products / {before_sql} SQL / {before_ms:.2f} ms; after: {after_models} / {after_sql} SQL / {after_ms:.2f} ms')

    def test_hidden_out_of_stock_and_duplicates_keep_original_semantics(self):
        ids = list(Product.objects.order_by('pk').values_list('pk', flat=True))
        Product.objects.filter(pk=ids[0]).update(na_stanju=False)
        Product.objects.filter(pk=ids[1]).update(sakriven_do_stanja=True)
        sequence = [ids[0], ids[1], ids[2], ids[2], *ids[3:]]
        result = _selected_bestseller_products(sequence, self.request)
        self.assertEqual([p.pk for p in result], [ids[2], ids[2], *ids[3:7]])
        self.assertEqual(_selected_bestseller_products([], self.request), [])
