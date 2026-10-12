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

    def test_fragment_preserves_cards_and_filters_with_smaller_response(self):
        from .models import Category
        category = Category.objects.create(naziv='Rezultati', slug='rezultati')
        Product.objects.update(kategorija=category, na_stanju=True, stanje=5)
        for url in ('/?q=Rezultat&page=2', '/kategorija/rezultati/?page=2'):
            full = self.client.get(url)
            fragment = self.client.get(url, HTTP_X_CATALOG_FRAGMENT='1')
            self.assertEqual(full.status_code, 200)
            self.assertEqual(fragment.status_code, 200)
            self.assertNotContains(fragment, '<html')
            self.assertEqual(fragment.content.count(b'data-product-card'), 20)
            self.assertIn('X-Catalog-Fragment', fragment.headers['Vary'])
            self.assertIn('X-Catalog-Fragment', full.headers['Vary'])
            self.assertLess(len(fragment.content), len(full.content))
            self.assertContains(fragment, 'page=3')
            print(f'Fragment {url}: {len(full.content)} -> {len(fragment.content)} bytes')
