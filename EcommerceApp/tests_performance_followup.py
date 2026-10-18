from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
from django.db.models.signals import post_init
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from .admin import ProductAdmin
from .models import Category, Product, ProductVariation, Uvoz, UvozStavka
from .views import HOME_SECTION_PRODUCT_LIMIT, _home_latest_products


class PerformanceFollowupTests(TestCase):
    def test_new_arrivals_materialize_only_visible_cards(self):
        batch = Uvoz.objects.create(naziv='Performance import')
        products = [Product.objects.create(naziv=f'Import {i}', cijena=10, stanje=3, na_stanju=True)
                    for i in range(HOME_SECTION_PRODUCT_LIMIT + 12)]
        for i, product in enumerate(products):
            UvozStavka.objects.create(uvoz=batch, product=product, status='updated', redoslijed=i)
        loaded = []
        def count(sender, instance, **kwargs):
            loaded.append(instance.pk)
        post_init.connect(count, sender=Product, weak=False)
        try:
            result = _home_latest_products()
        finally:
            post_init.disconnect(count, sender=Product)
        self.assertEqual([p.pk for p in result], [p.pk for p in products[:HOME_SECTION_PRODUCT_LIMIT]])
        self.assertLessEqual(len(loaded), HOME_SECTION_PRODUCT_LIMIT)

    def test_warehouse_variation_image_fallback_is_batched(self):
        from .views_magacin import magacin_artikli
        products = [Product.objects.create(naziv=f'Image lookup {i}', cijena=10) for i in range(5)]
        for product in products:
            ProductVariation.objects.create(artikal=product, naziv='Image', slika='products/variations/test.png')
        request = RequestFactory().get('/nalog/magacin/artikli/', {'pretraga': 'Image lookup'})
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True, is_active=True)
        request.session = {}
        with patch('EcommerceApp.views_magacin._ensure_magacin_locations'), \
             patch('EcommerceApp.views_magacin._magacin_context', return_value={}), \
             patch('EcommerceApp.views_magacin.search_products', return_value=(Product.objects.filter(pk__in=[p.pk for p in products]), False)), \
             patch('EcommerceApp.views_magacin.render', side_effect=lambda r, t, c: c):
            with CaptureQueriesContext(connection) as queries:
                context = magacin_artikli(request)
            variation_queries = [q for q in queries if 'FROM "EcommerceApp_productvariation"' in q['sql']]
            self.assertEqual(len(variation_queries), 1)
        with self.assertNumQueries(0):
            for row in context['page']:
                self.assertEqual(row['product'].prikazna_slika.name, 'products/variations/test.png')
                self.assertEqual(row['na_stanju'], 0)

    def test_admin_category_parent_does_not_add_per_row_queries(self):
        parent = Category.objects.create(naziv='Parent')
        child = Category.objects.create(naziv='Child', roditelj=parent)
        for i in range(5):
            Product.objects.create(naziv=f'Admin {i}', cijena=10, kategorija=child)
        model_admin = ProductAdmin(Product, admin.site)
        request = RequestFactory().get('/admin/EcommerceApp/product/')
        with self.assertNumQueries(1):
            labels = [str(p.kategorija) for p in model_admin.get_queryset(request)]
        self.assertEqual(labels, ['Parent → Child'] * 5)

    def test_removed_exit_ui_does_not_build_context_or_mutate_session(self):
        from .context_processors import nav_categories
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        request.session = SessionStore()
        with patch('EcommerceApp.cart_exit_popup.get_cart_exit_popup_context') as offer, \
             patch('EcommerceApp.cart_exit_popup.get_cart_abandon_exit_context') as abandon:
            context = nav_categories(request)
        offer.assert_not_called()
        abandon.assert_not_called()
        self.assertIsNone(context['cart_exit_popup'])
        self.assertIsNone(context['cart_abandon_exit'])

    def test_category_tree_uses_depth_queries_and_preserves_active_branches(self):
        root = Category.objects.create(naziv='Tree')
        children = [Category.objects.create(naziv=f'Branch {i}', roditelj=root) for i in range(8)]
        leaf = Category.objects.create(naziv='Leaf', roditelj=children[0])
        inactive = Category.objects.create(naziv='Hidden', roditelj=root, aktivan=False)
        Category.objects.create(naziv='Excluded leaf', roditelj=inactive)
        with self.assertNumQueries(3):
            actual = root.get_descendant_ids()
        self.assertEqual(actual, [root.pk, children[0].pk, leaf.pk] + [p.pk for p in children[1:]])

    def test_category_tree_respects_filtered_prefetch(self):
        from django.db.models import Prefetch
        root = Category.objects.create(naziv='Prefetch tree')
        first = Category.objects.create(naziv='Included', roditelj=root)
        Category.objects.create(naziv='Excluded', roditelj=root)
        root = Category.objects.prefetch_related(Prefetch('podkategorije', queryset=Category.objects.filter(pk=first.pk))).get(pk=root.pk)
        self.assertEqual(root.get_descendant_ids(), [root.pk, first.pk])
