from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import LoyaltyCard, LoyaltyRules, SiteModule, Product, StoreSettings, Order


class CheckoutPointsPreviewTests(TestCase):
    def setUp(self):
        SiteModule.objects.update_or_create(code='loyalty', defaults={'enabled': True})
        self.user = get_user_model().objects.create_user('preview-buyer')
        self.card, _ = LoyaltyCard.objects.get_or_create(customer=self.user)
        self.card.points = 1000
        self.card.save()
        LoyaltyRules.objects.create(redemption_points=100, point_value=5, max_discount_percent=50)
        StoreSettings.objects.create(pk=1, delivery_price=10, free_delivery_threshold=100)
        self.product = Product.objects.create(name='Preview', price=100)
        self.client.force_login(self.user)
        session = self.client.session
        session['cart'] = {str(self.product.pk): 1}
        session.save()
        self.url = reverse('checkout')

    def preview(self, points):
        return self.client.get(self.url, {'points_preview': '1', 'points': points})

    def test_preview_discount_total_and_delivery_match_final_order(self):
        response = self.preview(100)
        data = response.json()
        self.assertEqual(Decimal(data['points_discount']), Decimal('5'))
        self.assertEqual(Decimal(data['delivery_price']), Decimal('10'))
        self.assertEqual(Decimal(data['grand_total']), Decimal('105'))
        self.assertEqual(self.preview(0).json()['grand_total'], '100.00')
        self.assertIn('no-store', response['Cache-Control'])
        self.card.refresh_from_db()
        self.assertEqual(self.card.points, 1000)
        self.assertFalse(Order.objects.exists())
        self.client.post(self.url, dict(customer_name='Kupac', email='buyer@example.com', phone='061123456', address='Ulica 1', city='Sarajevo', postal_code='71000', note='Test', loyalty_points=100))
        order = Order.objects.get()
        self.assertEqual(order.total, Decimal(data['grand_total']))
        self.assertEqual(order.loyalty_discount, Decimal('5'))

    def test_preview_clamps_to_balance_and_percentage_limit(self):
        data = self.preview(99999).json()
        self.assertEqual(data['points_used'], 1000)
        self.assertEqual(Decimal(data['points_discount']), Decimal('50'))
        self.assertEqual(Decimal(data['grand_total']), Decimal('60'))
        for points in [-100, 'invalid']:
            self.assertEqual(self.preview(points).json()['points_used'], 0)

    def test_disabled_points_and_guests_get_no_discount(self):
        LoyaltyRules.objects.filter(pk=1).update(points_enabled=False)
        self.assertEqual(self.preview(100).json()['points_used'], 0)
        self.client.logout()
        session = self.client.session
        session['cart'] = {str(self.product.pk): 1}
        session.save()
        self.assertEqual(self.preview(100).json()['points_used'], 0)
