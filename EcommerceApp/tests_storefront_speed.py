from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from . import views
from .models import Product


class StorefrontSpeedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.product = Product.objects.create(naziv='Speed QA', cijena=10,
                                              na_stanju=True, stanje=5, opis='Large description ' * 500)
        self.request = RequestFactory().get('/')
        self.request.user = AnonymousUser()
        self.request.session = {}

    def test_curated_home_selection_skips_unused_random_cards(self):
        with patch('EcommerceApp.bera_home.homepage_bestseller_ids', return_value=[self.product.pk] * 2), patch(
            'EcommerceApp.views._home_latest_products_uncached'
        ) as fallback:
            cards = views._home_latest_products(self.request)
        self.assertEqual([card.pk for card in cards], [self.product.pk] * 2)
        fallback.assert_not_called()
        self.assertIn('opis', cards[0].get_deferred_fields())

    def test_no_curated_selection_preserves_random_fallback(self):
        with patch('EcommerceApp.bera_home.homepage_bestseller_ids', return_value=[]), patch(
            'EcommerceApp.views._home_latest_products_uncached', return_value=[self.product]
        ) as fallback:
            self.assertEqual(views._home_latest_products(self.request), [self.product])
        fallback.assert_called_once_with(self.request)

    def test_staff_edit_mode_does_not_poison_public_product_cache(self):
        self.request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)
        self.request.session = {'staff_edit_mode': True}
        cases = [('_home_featured_products', '_home_featured_products_uncached'),
                 ('_home_sale_products', '_home_sale_products_uncached'),
                 ('_home_brand_showcases', '_home_brand_showcases_uncached'),
                 ('_home_category_showcases', '_home_category_showcases_uncached')]
        for public, factory in cases:
            with self.subTest(section=public), patch.object(views, factory, return_value=[self.product]), patch.object(
                views, '_home_cache_get'
            ) as shared:
                self.assertEqual(getattr(views, public)(self.request), [self.product])
                shared.assert_not_called()
