"""Public search contract: SKU, folded text, filters and both result endpoints."""
import json
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.db import connection
from django.test import TestCase, RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext

from .models import Category, Product, ProductVariation, Tag
from .search_expressions import fold
from .views import _apply_product_filters, _apply_search_filter, _rank_search_queryset, search_suggest


@override_settings(SITE_PREP_ENABLED=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SearchRankingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(naziv='Čizme')
        cls.child = Category.objects.create(naziv='Zimske čizme', roditelj=cls.category)
        cls.boot = Product.objects.create(naziv='MT12221 MATE HP WINTER BOOTS 41', sifra='ODOO-T5672', cijena=100, kategorija=cls.child)
        cls.code = Product.objects.create(naziv='Rasprodati original', sifra='AB-12345', cijena=20, na_stanju=False)
        cls.partial = Product.objects.create(naziv='AB12345 rezervni dio', sifra='AB12345-extra', cijena=20, na_stanju=True)
        cls.variant_product = Product.objects.create(naziv='Artikal s varijacijom', cijena=20)
        ProductVariation.objects.create(artikal=cls.variant_product, naziv='Plava', sifra='VAR-9876')
        cls.okuma = Product.objects.create(naziv='Okuma mašinica 4000', cijena=40)
        cls.ascii = Product.objects.create(naziv='Okuma masinica 4000 B', cijena=40)
        cls.other_size = Product.objects.create(naziv='Okuma mašinica 3000', cijena=30)
        cls.wrong_numeric = Product.objects.create(naziv='Okuma AZORES ZXP-14000P mašinica', cijena=40)
        cls.other_brand = Product.objects.create(naziv='Shimano mašinica 4000', cijena=40)
        cls.rod = Product.objects.create(naziv='Štap za ribolov', cijena=10)
        cls.dj = Product.objects.create(naziv='Đak ribolovački', cijena=10)
        for i in range(7):
            Product.objects.create(naziv=f'Mašinica za pecanje {i}', cijena=10)
        cls.tag_only = Product.objects.create(naziv='Dodatni pribor', cijena=10)
        cls.tag_only.tagovi.add(Tag.objects.create(naziv='okuma masinica 4000'))
        Product.objects.create(naziv='Sakrivena mašinica', cijena=10, sakriven_do_stanja=True)
        Product.objects.create(naziv='Neaktivna mašinica', cijena=10, aktivan=False)

    def request(self, params, path='/'):
        request = RequestFactory().get(path, params)
        request.user = AnonymousUser()
        request.session = SessionStore()
        return request

    def full(self, query, **filters):
        from .views import _product_queryset
        request = self.request({'q': query, **filters})
        with patch('EcommerceApp.views.SiteSearchEvent.objects.create'):
            products, _ = _apply_product_filters(_product_queryset(request), request)
        return [p.slug for p in products]

    def suggest(self, query, **filters):
        return json.loads(search_suggest(self.request({'q': query, **filters}, '/api/pretraga/')).content)

    def test_code_spelling_case_separators_and_name_only_manufacturer_code(self):
        for query in ('MT12221', 'mt12221', 'MT-12221', 'MT 12221', '12221', 'ODOO-T5672'):
            with self.subTest(query=query):
                self.assertEqual(self.full(query)[0], self.boot.slug)
                self.assertEqual(self.suggest(query)['results'][0]['slug'], self.boot.slug)
        for query in ('AB12345', 'ab-12345', 'AB 12345'):
            self.assertEqual(self.full(query)[0], self.code.slug)
            self.assertNotIn(self.code.slug, self.full(query, na_stanju='1'))
        for query in ('VAR9876', 'var-9876', 'VAR 9876'):
            self.assertEqual(self.full(query)[0], self.variant_product.slug)

    def test_accent_equivalence_preserves_entire_order(self):
        for left, right in [('mašinica', 'masinica'), ('štap', 'stap'), ('đak', 'djak'), ('MAS\u030cINICA', 'masinica')]:
            self.assertEqual(fold(left), fold(right))
            self.assertEqual(self.full(left), self.full(right))
            self.assertEqual(self.suggest(left)['results'], self.suggest(right)['results'])

    def test_all_words_beat_tag_only_and_other_sizes(self):
        results = self.full('okuma mašinica 4000')
        self.assertEqual(results[:2], [self.okuma.slug, self.ascii.slug])
        self.assertNotIn(self.other_size.slug, results)
        self.assertNotIn(self.other_brand.slug, results)
        self.assertNotIn(self.wrong_numeric.slug, results)
        self.assertLess(results.index(self.okuma.slug), results.index(self.tag_only.slug))

    def test_word_order_and_typo(self):
        self.assertIn(self.boot.slug, self.full('boots winter'))
        self.assertIn(self.boot.slug, self.full('wintr boots'))
        rows = _rank_search_queryset(_apply_search_filter(Product.objects.all(), 'boots winter'), 'boots winter')
        exact = rows.get(pk=self.boot.pk)._search_sql_rel
        rows = _rank_search_queryset(_apply_search_filter(Product.objects.all(), 'wintr boots'), 'wintr boots')
        self.assertGreater(exact, rows.get(pk=self.boot.pk)._search_sql_rel)

    def test_first_five_api_match_full_results_for_identical_filters(self):
        for query, filters in [('mašinica', {}), ('masinica', {}), ('boots winter', {'kategorija': self.category.slug}),
                               ('AB12345', {}), ('AB12345', {'na_stanju': '1'}),
                               ('mašinica', {'cijena_od': '35'}), ('mašinica', {'sort': 'rastuca'})]:
            with self.subTest(query=query, filters=filters):
                data = self.suggest(query, **filters)
                full = self.full(query, **filters)
                self.assertEqual([r['slug'] for r in data['results']], full[:5])
                self.assertEqual(data['has_more'], len(full) > 5)
                self.assertEqual(set(data), {'results', 'query', 'has_more'})
        self.assertEqual(len(self.suggest('mašinica', limit='99')['results']), 5)
        self.assertEqual(self.suggest('boots winter', kategorija='missing')['results'], [])

    def test_search_pipeline_has_bounded_queries_and_typo_candidate_limit(self):
        for query in ('mašinica', 'MT12221', 'wintr boots'):
            with CaptureQueriesContext(connection) as captured:
                rows = list(_rank_search_queryset(_apply_search_filter(Product.objects.filter(aktivan=True), query), query)[:6])
            self.assertTrue(rows)
            self.assertLessEqual(len(captured), 4)
            if query == 'wintr boots':
                self.assertTrue(any('LIMIT 300' in q['sql'] for q in captured))

    def test_public_page_is_noindex_and_keeps_pagination(self):
        response = self.client.get('/', {'q': 'mašinica'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['meta_robots_content'], 'noindex, follow')
        self.assertEqual([p.slug for p in response.context['search_products']][:5], self.full('mašinica')[:5])
        self.assertEqual(response.context['page_obj'].paginator.per_page, 49)

    def test_bulk_import_does_not_need_precomputed_normalized_fields(self):
        Product.objects.filter(pk=self.boot.pk).update(naziv='MT-12221 MATE čizme', naziv_normalized='stari naziv')
        self.assertEqual(self.full('MT 12221')[0], self.boot.slug)
        self.assertEqual(self.suggest('cizme')['results'][0]['slug'], self.boot.slug)

    def test_public_pagination_and_api_are_consistent_after_bulk_create(self):
        Product.objects.bulk_create([Product(naziv=f'Paginacija mašinica {i:02}',
            slug=f'search-page-fixture-{i}', cijena=10) for i in range(55)])
        first = self.client.get('/', {'q': 'paginacija masinica'})
        second = self.client.get('/', {'q': 'paginacija masinica', 'page': 2})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        page_one = [p.slug for p in first.context['search_products']]
        page_two = [p.slug for p in second.context['search_products']]
        self.assertEqual(len(page_one), 49)
        self.assertEqual(len(page_two), 6)
        self.assertFalse(set(page_one) & set(page_two))
        self.assertEqual([r['slug'] for r in self.suggest('paginacija masinica')['results']], page_one[:5])

    def test_search_paginates_before_loading_product_objects(self):
        from .search_results import SearchResults
        from .views import _product_queryset, _paginate_catalog_products
        Product.objects.bulk_create([Product(naziv=f'Brza pretraga {i:03}',
            slug=f'brza-pretraga-{i}', cijena=10) for i in range(120)])
        request = self.request({'q': 'brza pretraga'})
        products, _ = _apply_product_filters(_product_queryset(request), request, record_search=False)
        self.assertIsInstance(products, SearchResults)
        self.assertIsNone(products.queryset._result_cache)
        with self.assertNumQueries(1):
            self.assertEqual(products.count(), 120)
            self.assertEqual(len(products), 120)
        page = _paginate_catalog_products(request, products)
        self.assertIsNone(products.queryset._result_cache)
        rows = list(page.object_list)
        self.assertEqual(len(rows), 49)
        self.assertIsNone(products.queryset._result_cache)
        self.assertEqual(page.paginator.count, 120)

    def test_size_filters_reuse_request_search_preparation(self):
        from .views import _product_queryset, _filter_size_scope_qs, _fuzzy_name_product_ids
        request = self.request({'q': 'wintr boots'})
        with patch('EcommerceApp.views._fuzzy_name_product_ids', wraps=_fuzzy_name_product_ids) as fuzzy:
            products, params = _apply_product_filters(_product_queryset(request), request, record_search=False)
            _filter_size_scope_qs(params, request=request)
            self.assertEqual(fuzzy.call_count, 1)
            self.assertEqual(list(products)[0].pk, self.boot.pk)

    def test_suggestions_read_latest_price_without_second_price_select(self):
        Product.objects.filter(pk=self.boot.pk).update(cijena='123.45')
        with CaptureQueriesContext(connection) as captured:
            data = self.suggest('MT12221')
        self.assertEqual(data['results'][0]['price'], '123.45')
        self.assertFalse(any('AS "cijena"' in row['sql'] and 'AS "magacin_sync_at"' in row['sql'] for row in captured))

    def test_suggestions_only_return_image_name_price_and_navigation(self):
        with CaptureQueriesContext(connection) as queries:
            data = self.suggest('MT12221')
        self.assertEqual(set(data['results'][0]), {'naziv', 'slug', 'url', 'image', 'price'})
        self.assertFalse(any('EcommerceApp_warehousestock' in q['sql'] for q in queries))
        self.assertFalse(any('EcommerceApp_category' in q['sql'] for q in queries))
        self.assertFalse(any('"opis"' in q['sql'] for q in queries))

    def test_full_search_uses_simple_cards_without_cart_or_extra_filters(self):
        response = self.client.get('/', {'q': 'MT12221'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['simple_search'])
        self.assertEqual(response.context['simple_search_items'][0]['slug'], self.boot.slug)
        self.assertEqual(response.context['filter_size_groups'], [])
        self.assertNotContains(response, 'data-catalog-add')
        self.assertEqual(response.context['meta_robots_content'], 'noindex, follow')
