from django.test import TestCase, RequestFactory
from django.db import connection
from django.test.utils import CaptureQueriesContext
from .models import Product, ProductVariation, Category
from .views import _product_queryset, _category_ranking_queryset, _apply_product_filters


class CategoryRankingPerformanceTests(TestCase):
    def test_lightweight_ranking_preserves_prices_variations_and_order(self):
        category = Category.objects.create(naziv='Performance')
        for index in range(80):
            product = Product.objects.create(naziv=f'Artikal {index:03}', kategorija=category, cijena=80-index, opis='Dugi opis ' * 1000, na_stanju=index % 3 != 0, prioritet_lagera=index % 3)
            if index % 5 == 0:
                ProductVariation.objects.create(artikal=product, naziv='Varijacija', cijena=5, na_stanju=True)
        request = RequestFactory().get('/kategorija/performance/')
        baseline, _ = _apply_product_filters(_product_queryset(request), request)
        with CaptureQueriesContext(connection) as captured:
            optimized, _ = _apply_product_filters(_category_ranking_queryset(_product_queryset(request)), request)
        self.assertEqual([p.pk for p in baseline], [p.pk for p in optimized])
        self.assertTrue(all('opis' in p.get_deferred_fields() for p in optimized))
        self.assertLessEqual(len(captured), 4)
        product_sql = next(row['sql'] for row in captured if 'FROM "EcommerceApp_product"' in row['sql'])
        self.assertNotIn('"opis"', product_sql)

    def test_category_loads_full_cards_only_for_current_page(self):
        from unittest.mock import patch
        from django.http import HttpResponse
        from django.core.cache import cache
        cache.clear()
        category = Category.objects.create(naziv='Kartice', slug='kartice')
        for index in range(55):
            Product.objects.create(naziv=f'Kartica {index}', kategorija=category, cijena=index+1, opis='Opis proizvoda', na_stanju=True)
        def render(request, template, context):
            self.assertEqual(context['page_obj'].paginator.count, 55)
            self.assertEqual(len(context['products']), 49)
            self.assertTrue(all('opis' not in p.get_deferred_fields() for p in context['products']))
            return HttpResponse('OK')
        with patch('EcommerceApp.bera_home.render_storefront', side_effect=render):
            response = self.client.get('/kategorija/kartice/')
        self.assertEqual(response.status_code, 200)
