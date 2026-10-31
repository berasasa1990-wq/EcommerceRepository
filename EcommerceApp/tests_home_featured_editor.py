from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from .forms import HomeFeaturedProductsForm
from .models import HomeFeaturedProduct, Product, SiteSettings
from .views import staff_home_page, _home_featured_products_uncached


class FeaturedEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('featured-admin', 'featured@example.com', 'password')
        cls.products = [Product.objects.create(naziv=f'Featured {i}', sifra=f'FEATURED-{i}', cijena='10', aktivan=True, na_stanju=True, stanje=10) for i in range(7)]

    def request(self, data):
        request = RequestFactory().post('/nalog/admin/pocetna-stranica/', data)
        request.user = self.admin
        return request

    def test_reject_more_than_six(self):
        form = HomeFeaturedProductsForm(self.request({'artikli': [p.pk for p in self.products]}).POST)
        self.assertFalse(form.is_valid())
        self.assertIn('artikli', form.errors)

    def test_accept_six_featured_products(self):
        form = HomeFeaturedProductsForm(self.request({'artikli': [p.pk for p in self.products[:6]]}).POST)
        self.assertTrue(form.is_valid(), form.errors)

    @patch('EcommerceApp.views.messages.success')
    @patch('EcommerceApp.views._invalidate_storefront_product_caches')
    def test_save_replaces_selection_and_invalidates_cache(self, invalidate, success):
        settings = SiteSettings.load()
        HomeFeaturedProduct.objects.create(postavke=settings, artikal=self.products[0])
        response = staff_home_page(self.request({'artikli': [self.products[1].pk, self.products[2].pk]}))
        self.assertEqual(response.status_code, 302)
        self.assertSetEqual(set(HomeFeaturedProduct.objects.filter(postavke=settings).values_list('artikal_id', flat=True)), {self.products[1].pk, self.products[2].pk})
        invalidate.assert_called_once()

    def test_manual_selection_has_priority_and_omits_unavailable(self):
        settings = SiteSettings.load()
        HomeFeaturedProduct.objects.create(postavke=settings, artikal=self.products[1], redoslijed=0)
        HomeFeaturedProduct.objects.create(postavke=settings, artikal=self.products[0], redoslijed=1)
        self.products[0].na_stanju = False
        self.products[0].stanje = 0
        self.products[0].save()
        self.assertEqual([p.pk for p in _home_featured_products_uncached()], [self.products[1].pk])

    def test_non_superuser_cannot_edit(self):
        request = self.request({'artikli': [self.products[1].pk]})
        request.user.is_superuser = False
        self.assertEqual(staff_home_page(request).status_code, 302)
