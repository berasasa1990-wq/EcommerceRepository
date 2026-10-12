from django.test import TestCase, RequestFactory
from django.template.loader import render_to_string
from .models import Product
from .views import _paginate_catalog_products


class CatalogLoadMoreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Product.objects.bulk_create([
            Product(naziv=f'Rezultat {i}', slug=f'rezultat-{i}', cijena=10)
            for i in range(45)
        ])

    def test_pages_are_twenty_products_without_duplicates(self):
        products = Product.objects.order_by('pk')
        pages = [_paginate_catalog_products(RequestFactory().get('/', {'page': i}), products)
                 for i in (1, 2, 3)]
        self.assertEqual([len(page.object_list) for page in pages], [20, 20, 5])
        ids = [item.pk for page in pages for item in page.object_list]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(pages[-1].has_next())

    def test_next_link_preserves_filters_and_last_page_hides_button(self):
        products = Product.objects.order_by('pk')
        context = {'filter_action': '/', 'catalog_query': 'q=slim+fish&brend=2'}
        page = _paginate_catalog_products(RequestFactory().get('/'), products)
        html = render_to_string('bera_reference/partials/catalog_load_more.html', dict(context, page_obj=page))
        self.assertIn('q=slim+fish&amp;brend=2&amp;page=2', html)
        self.assertNotIn('Prikazano', html)
        catalog = render_to_string('bera_reference/partials/product_catalog.html', dict(context, page_obj=page))
        self.assertNotIn('catalog-pagination', catalog)
        self.assertIn('Prikaži više', catalog)
        last = _paginate_catalog_products(RequestFactory().get('/', {'page': 3}), products)
        html = render_to_string('bera_reference/partials/catalog_load_more.html', dict(context, page_obj=last))
        self.assertNotIn('data-catalog-load-more', html)
