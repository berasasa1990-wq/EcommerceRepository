from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.utils import timezone

from .forms import SaleForm
from .models import Product
from .promotions import expire_promotions


class PromotionTimingTests(TestCase):
    def test_expiry_restores_prices_and_disables_every_promotion_type(self):
        for kind in Product.SaleType.values:
            Product.objects.create(name=kind, price=80, original_price=100,
                                   sale_type=kind, plus_percent=20, bundle_percent=20,
                                   quantity_percent_2=10,
                                   sale_ends_at=timezone.now() - timedelta(seconds=1))
        future = Product.objects.create(name='Future', price=80, original_price=100,
                                        sale_ends_at=timezone.now() + timedelta(days=1))
        unlimited = Product.objects.create(name='Unlimited', price=80, original_price=100)
        expire_promotions()
        for product in Product.objects.exclude(pk__in=[future.pk, unlimited.pk]):
            self.assertEqual(product.price, 100)
            self.assertIsNone(product.original_price)
            self.assertEqual(product.sale_type, 'regular')
            self.assertIsNone(product.sale_ends_at)
            self.assertIsNone(product.plus_percent)
            self.assertIsNone(product.bundle_percent)
            self.assertIsNone(product.quantity_percent_2)
        future.refresh_from_db()
        unlimited.refresh_from_db()
        self.assertEqual(future.price, 80)
        self.assertEqual(unlimited.price, 80)

    def test_panel_saves_end_time_in_sarajevo_time_and_can_remove_it(self):
        manager = get_user_model().objects.create_user('timer-manager', is_staff=True)
        self.client.force_login(manager)
        product = Product.objects.create(name='Timed', price=100)
        data = {'sale_type': 'regular', 'product': product.pk, 'discount_percent': 20,
                'sale_ends_at': '2030-01-10T14:30'}
        response = self.client.post('/panel/akcije/dodaj/', data)
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.sale_ends_at.hour, 13)
        self.assertEqual(product.price, 80)
        self.assertIn('2030-01-10T14:30', str(SaleForm(instance=product)['sale_ends_at']))
        data['sale_ends_at'] = ''
        self.assertEqual(self.client.post(f'/panel/akcije/{product.pk}/uredi/', data).status_code, 302)
        product.refresh_from_db()
        self.assertIsNone(product.sale_ends_at)

    def test_rejects_past_deadline(self):
        product = Product.objects.create(name='Test', price=100)
        form = SaleForm(data={'product': product.pk, 'sale_type': 'regular',
                              'discount_percent': 20, 'sale_ends_at': '2000-01-01T12:00'})
        self.assertFalse(form.is_valid())
        self.assertIn('sale_ends_at', form.errors)

    def test_request_expires_promotion_before_cart_pricing(self):
        product = Product.objects.create(name='Expired', price=80, original_price=100,
                                        sale_ends_at=timezone.now() - timedelta(seconds=1))
        session = self.client.session
        session['cart'] = {str(product.pk): 1}
        session.save()
        response = self.client.get('/korpa/')
        self.assertEqual(response.context['cart_total'], Decimal('100'))

    def test_card_places_timer_after_brand_and_omits_it_without_deadline(self):
        product = Product.objects.create(name='Timed', price=100,
                                        sale_ends_at=timezone.now() + timedelta(days=1))
        html = render_to_string('shop/partials/products.html', {'products': [product]})
        self.assertIn('data-sale-countdown=', html)
        self.assertIn('Akcija ističe za', html)
        product.sale_ends_at = None
        html = render_to_string('shop/partials/products.html', {'products': [product]})
        self.assertNotIn('data-sale-countdown=', html)
