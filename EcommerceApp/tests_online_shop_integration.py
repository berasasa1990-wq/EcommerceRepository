"""The imported design must use the original catalog and commerce routes."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from . import views, views_b2b, views_monri, views_partner_stock
from .models import Brand, Category, Product, ProductVariation


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
    SITE_PREP_ENABLED=False,
)
class OnlineShopIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.brand = Brand.objects.create(naziv='Izvorni brend')
        cls.category = Category.objects.create(naziv='Izvorna kategorija')
        cls.product = Product.objects.create(
            naziv='Izvorni artikal', sifra='ORIGINAL-123', cijena='29.00',
            stanje=7, kategorija=cls.category, brend=cls.brand,
        )
        cls.variation = ProductVariation.objects.create(
            artikal=cls.product, naziv='Izvorna varijacija', stanje=7,
        )

    def test_home_uses_original_product_ids_prices_and_variations(self):
        with patch('EcommerceApp.views._home_featured_products', return_value=[self.product]):
            response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'online-shop-home-content')
        self.assertContains(response, self.product.naziv)
        self.assertContains(response, self.product.get_absolute_url())
        self.assertContains(response, f'data-product-id="{self.product.pk}"')
        self.assertContains(response, f'data-variation-id="{self.variation.pk}"')
        self.assertContains(response, f'data-product-slug="{self.product.slug}"')
        self.assertNotContains(response, 'data-cart-add=')

    def test_brand_and_category_links_use_original_routes(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, '?brend=' + self.brand.slug)
        self.assertContains(response, self.category.get_absolute_url())
        self.assertContains(response, reverse('b2b_catalog'))

    def test_old_search_still_uses_the_original_catalog(self):
        response = self.client.get(reverse('home'), {'q': 'ORIGINAL-123'})
        self.assertContains(response, self.product.naziv)
        self.assertContains(response, self.product.get_absolute_url())

    def test_product_and_cart_render_with_new_chrome(self):
        for path in [self.product.get_absolute_url(), reverse('cart')]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'online-shop-theme')
                self.assertContains(response, 'brandsDialog')

    def test_commerce_handlers_are_original(self):
        self.assertIs(resolve(reverse('monri_callback')).func, views_monri.callback)
        self.assertIs(resolve(reverse('b2b_catalog')).func, views_b2b.catalog)
        self.assertIs(resolve(reverse('partner_stock_products')).func, views_partner_stock.products)
        self.assertIs(resolve(reverse('home')).func, views.home)
        self.assertEqual(resolve(reverse('staff_magacin')).url_name, 'staff_magacin')
        self.assertEqual(resolve(reverse('catalog_api_ping')).url_name, 'catalog_api_ping')

    def test_b2b_keeps_its_existing_workspace(self):
        response = self.client.get(reverse('b2b_catalog'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'css/online-shop/integration.css')

    def test_staff_magacin_keeps_its_existing_workspace(self):
        admin = get_user_model().objects.create_superuser('integration-admin', 'integration@example.com', 'test-only')
        self.client.force_login(admin)
        response = self.client.get(reverse('staff_magacin'), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'css/online-shop/integration.css')
