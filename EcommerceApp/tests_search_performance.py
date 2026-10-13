from unittest.mock import patch
from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from .models import Product, ProductVariation
from .views import _apply_product_filters, _product_queryset, _search_ranking_queryset


class SearchPerformanceTests(TestCase):
    def setUp(self):
        for i in range(80):
            p = Product.objects.create(naziv=f'Slim fish {i:03}', sifra=f'SLIM-{i}',
                cijena=100-i, opis='Dugi opis ' * 2000,
                prioritet_lagera=i % 3, na_stanju=i % 4 != 0, stanje=10,
                je_novitet=i % 7 == 0)
            if i % 5 == 0:
                ProductVariation.objects.create(artikal=p, naziv='Varijacija',
                    sifra=f'VAR-{i}', cijena=5, na_stanju=True)

    def test_rank_order_is_identical_without_loading_descriptions(self):
        for query in ('slim fish', 'slim fsh'):
            for sort in ('', 'opadajuca'):
                with self.subTest(query=query, sort=sort):
                    request = RequestFactory().get('/nalog/', {'q': query, 'sort': sort})
                    baseline, _ = _apply_product_filters(_product_queryset(request), request)
                    with CaptureQueriesContext(connection) as queries:
                        light, _ = _apply_product_filters(_search_ranking_queryset(_product_queryset(request)), request)
                    self.assertEqual([p.pk for p in baseline], [p.pk for p in light])
                    self.assertTrue(all('opis' in p.get_deferred_fields() for p in light))
                    self.assertLessEqual(len(queries), 8)
                    sql = next(row['sql'] for row in queries if 'FROM "EcommerceApp_product"' in row['sql'])
                    self.assertNotIn('"EcommerceApp_product"."opis"', sql)

    def test_enter_results_load_full_cards_only_for_current_page_and_skip_banners(self):
        from django.http import HttpResponse
        def render(request, template, context):
            self.assertEqual(context['page_obj'].paginator.count, 80)
            self.assertEqual(len(context['search_products']), 20)
            self.assertTrue(all('opis' not in p.get_deferred_fields() for p in context['search_products']))
            return HttpResponse('OK')
        with patch('EcommerceApp.views.render', side_effect=render), patch('EcommerceApp.views._banners_with_media') as banners:
            response = self.client.get('/?q=slim+fish')
            self.assertEqual(response.status_code, 200)
            banners.assert_not_called()

    def test_typo_matching_is_computed_once_for_results_and_size_filters(self):
        from django.http import HttpResponse
        from .views import _typo_tolerant_name_match
        with patch('EcommerceApp.views.render', return_value=HttpResponse('OK')), \
             patch('EcommerceApp.views._typo_tolerant_name_match', wraps=_typo_tolerant_name_match) as typo:
            self.assertEqual(self.client.get('/?q=slim+fsh').status_code, 200)
            self.assertEqual(typo.call_count, 1)
