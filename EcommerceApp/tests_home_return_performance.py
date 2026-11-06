from decimal import Decimal
from django.core.cache import cache
from django.template import Context, Template
from django.test import TestCase
from .models import Product
from .views import _refresh_home_product_sections


class HomeReturnPerformanceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.product = Product.objects.create(naziv='Povratak test', cijena='10.00', stanje=5, na_stanju=True)

    def test_sections_share_one_live_batch_and_tags_do_not_repeat_queries(self):
        context = {'latest_products': [self.product], 'bestselling_products': [self.product],
                   'featured_products': [self.product], 'sale_products': [self.product]}
        with self.assertNumQueries(4):
            _refresh_home_product_sections(context)
        template = Template('{% load ecommerce_extras %}{% live_stock_products latest_products as first %}'
                            '{% live_stock_products bestselling_products as second %}{{ first.0.pk }}:{{ second.0.pk }}')
        with self.assertNumQueries(0):
            self.assertEqual(template.render(Context(context)), f'{self.product.pk}:{self.product.pk}')

    def test_return_reads_current_prices_and_stock_even_with_old_section_objects(self):
        first = {'latest_products': [self.product]}
        _refresh_home_product_sections(first)
        Product.objects.filter(pk=self.product.pk).update(cijena='29.00', stanje=0, na_stanju=False)
        second = {'latest_products': [self.product]}
        _refresh_home_product_sections(second)
        refreshed = second['storefront_products_by_id'][self.product.pk]
        self.assertEqual(refreshed.cijena, Decimal('29.00'))
        self.assertEqual(refreshed.stanje, 0)
        self.assertFalse(refreshed.na_stanju)
        self.assertEqual(self.product.cijena, '10.00')

    def test_other_pages_still_refresh_stock_without_home_context(self):
        Product.objects.filter(pk=self.product.pk).update(stanje=2)
        template = Template('{% load ecommerce_extras %}{% live_stock_products products as live %}{{ live.0.stanje }}')
        self.assertEqual(template.render(Context({'products': [self.product]})), '2')
