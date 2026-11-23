from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.contrib.sessions.backends.db import SessionStore
from django.utils import timezone

from .cart import Cart
from .forms import HomeFeaturedProductsForm
from .home_timed_offers import active_home_offers
from .models import Akcija, HomeFeaturedProduct, HomeNovoProduct, Product, SiteSettings
from .views import staff_home_page, _home_featured_products_uncached, _home_latest_products_uncached


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

    @patch('EcommerceApp.views.messages.success')
    @patch('EcommerceApp.views._invalidate_storefront_product_caches')
    def test_new_arrivals_save_is_separate_from_featured(self, invalidate, success):
        settings = SiteSettings.load()
        HomeFeaturedProduct.objects.create(postavke=settings, artikal=self.products[0])
        HomeNovoProduct.objects.create(postavke=settings, artikal=self.products[1])
        response = staff_home_page(self.request({'section': 'new', 'artikli': [self.products[2].pk]}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(HomeNovoProduct.objects.values_list('artikal_id', flat=True)), [self.products[2].pk])
        self.assertEqual(list(HomeFeaturedProduct.objects.values_list('artikal_id', flat=True)), [self.products[0].pk])
        invalidate.assert_called_once()

    def test_no_automatic_new_arrivals_without_manual_selection(self):
        self.assertEqual(_home_latest_products_uncached(), [])

    def test_new_arrivals_follow_manual_order_and_visibility(self):
        settings = SiteSettings.load()
        HomeNovoProduct.objects.create(postavke=settings, artikal=self.products[2], redoslijed=0)
        HomeNovoProduct.objects.create(postavke=settings, artikal=self.products[1], redoslijed=1)
        HomeNovoProduct.objects.create(postavke=settings, artikal=self.products[0], redoslijed=2, aktivan=False)
        self.assertEqual([p.pk for p in _home_latest_products_uncached()], [self.products[2].pk, self.products[1].pk])

    def test_unknown_section_is_rejected(self):
        self.assertEqual(staff_home_page(self.request({'section': 'invalid'})).status_code, 400)

    @patch('EcommerceApp.views.messages.success')
    def test_timed_offers_save_separate_discounts_and_expire_in_cart(self, success):
        first, second = self.products[:2]
        cart_request = RequestFactory().get('/')
        cart_request.session = SessionStore()
        cart_request.user = self.admin
        cart = Cart(cart_request)
        self.assertEqual(cart.add(first), 1)
        self.assertEqual(cart.ukupno, Decimal('10.00'))
        form_data = {
            'section': Akcija.Tip.HOME_DAILY, 'enabled': 'on', 'artikli': [first.pk, second.pk],
            f'popust_{first.pk}': '25,50', f'popust_{second.pk}': '15',
        }
        self.assertEqual(staff_home_page(self.request(form_data)).status_code, 302)
        weekly_data = {
            'section': Akcija.Tip.HOME_WEEKLY, 'enabled': 'on', 'artikli': [first.pk],
            f'popust_{first.pk}': '10',
        }
        self.assertEqual(staff_home_page(self.request(weekly_data)).status_code, 302)
        daily = Akcija.objects.get(tip=Akcija.Tip.HOME_DAILY)
        weekly = Akcija.objects.get(tip=Akcija.Tip.HOME_WEEKLY)
        self.assertEqual(daily.zavrsava - daily.pocetak, timedelta(days=1))
        self.assertEqual(weekly.zavrsava - weekly.pocetak, timedelta(days=7))
        self.assertEqual(staff_home_page(self.request(form_data)).status_code, 302)
        self.assertEqual(Akcija.objects.get(pk=daily.pk).pocetak, daily.pocetak)
        self.assertEqual([offer['title'] for offer in active_home_offers(Product.objects.all())], [
            'Ponuda dana', 'Sedmična ponuda',
        ])
        self.assertEqual(cart.ukupno, Decimal('7.45'))
        second.refresh_from_db()
        self.assertEqual(second.prikazna_cijena, Decimal('8.50'))
        Akcija.objects.filter(pk=daily.pk).update(pocetak=timezone.now() - timedelta(days=2))
        self.assertEqual([offer['title'] for offer in active_home_offers(Product.objects.all())], ['Sedmična ponuda'])
        self.assertEqual(cart.ukupno, Decimal('9.00'))
        Akcija.objects.filter(pk=weekly.pk).update(pocetak=timezone.now() - timedelta(days=8))
        self.assertEqual(active_home_offers(Product.objects.all()), [])
        self.assertEqual(cart.ukupno, Decimal('10.00'))
