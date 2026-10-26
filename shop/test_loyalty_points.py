from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from .models import Order, LoyaltyCard, LoyaltyEntry, SiteModule, LoyaltyRules, Product
from .loyalty import settle_points, redeem_points


class LoyaltyPointsTests(TestCase):
    def setUp(self):
        SiteModule.objects.update_or_create(code='loyalty', defaults={'enabled': True})
        self.user = get_user_model().objects.create_user('points-user', password='test-password')
        self.other = get_user_model().objects.create_user('other-user')

    def order(self, **kwargs):
        return Order.objects.create(customer=self.user, total=Decimal('100'), delivery_price=Decimal('10'), **kwargs)

    def test_completed_only_and_idempotent(self):
        order = self.order()
        self.assertEqual(LoyaltyEntry.objects.count(), 0)
        order.status = 'completed'
        order.save()
        settle_points(order.pk)
        order.save()
        self.assertEqual(LoyaltyCard.objects.get(customer=self.user).points, 90)
        self.assertEqual(LoyaltyEntry.objects.count(), 1)

    def test_redemption_owner_limits_refund_and_reversal(self):
        award = self.order(status='completed')
        order = self.order()
        with self.assertRaises(PermissionDenied):
            redeem_points(order, self.other, Decimal('10'), 100)
        self.assertEqual(redeem_points(order, self.user, Decimal('1'), 999), Decimal('0.50'))
        with self.assertRaises(ValueError):
            redeem_points(order, self.user, Decimal('1'), 999)
        award.status = 'cancelled'
        award.save()
        self.assertEqual(LoyaltyCard.objects.get(customer=self.user).points, -50)
        order.status = 'cancelled'
        order.save()
        order.save()
        self.assertEqual(LoyaltyCard.objects.get(customer=self.user).points, 0)

    def test_disabled_backend_and_admin(self):
        order = self.order()
        SiteModule.objects.filter(code='loyalty').update(enabled=False)
        self.assertEqual(self.client.get(reverse('loyalty')).status_code, 403)
        with self.assertRaises(PermissionDenied):
            settle_points(order.pk)
        with self.assertRaises(PermissionDenied):
            redeem_points(order, self.user, Decimal('100'), 50)
        order.status = 'completed'
        order.save()
        self.assertFalse(LoyaltyEntry.objects.exists())
        admin = get_user_model().objects.create_superuser('points-admin', 'a@example.com', 'password')
        self.client.force_login(admin)
        for model in ['loyaltyrules', 'loyaltytier', 'loyaltycard', 'loyaltyentry']:
            self.assertEqual(self.client.get('/admin/shop/'+model+'/').status_code, 403)
        self.assertEqual(self.client.post('/admin/shop/loyaltyrules/add/', {'points_per_km': 100, 'point_value': 1, 'max_discount_percent': 100}).status_code, 403)
        self.assertFalse(LoyaltyRules.objects.exists())

    def test_page_private_history(self):
        order = self.order(status='completed')
        self.assertContains(self.client.get(reverse('loyalty')), 'Pridruži se programu')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('loyalty')), '#'+str(order.pk))
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('loyalty')), '#'+str(order.pk))

    def test_checkout_uses_server_balance_and_ignores_foreign_customer(self):
        self.order(status='completed')
        product = Product.objects.create(name='Points product', price=Decimal('100'))
        self.client.force_login(self.user)
        session = self.client.session
        session['cart'] = {str(product.pk): 1}
        session.save()
        response = self.client.post(reverse('checkout'), {'customer_name': 'Kupac', 'email': 'a@example.com', 'phone': '061123456', 'address': 'Ulica 1', 'city': 'Sarajevo', 'postal_code': '71000', 'loyalty_points': '99999', 'customer': self.other.pk})
        self.assertEqual(response.status_code, 302)
        order = Order.objects.latest('pk')
        self.assertEqual(order.customer, self.user)
        self.assertEqual(order.loyalty_discount, Decimal('0.90'))
        self.assertEqual(order.total, Decimal('99.10'))
        self.assertEqual(LoyaltyCard.objects.get(customer=self.user).points, 0)

    def test_shipped_order_awards_once_and_cancellation_reverses(self):
        order = self.order(status='shipped')
        card = LoyaltyCard.objects.get(customer=self.user)
        self.assertEqual(card.points, 90)
        order.status = 'completed'
        order.save()
        card.refresh_from_db()
        self.assertEqual(card.points, 90)
        self.assertEqual(order.point_entries.filter(kind='earn').count(), 1)
        order.status = 'cancelled'
        order.save()
        card.refresh_from_db()
        self.assertEqual(card.points, 0)

    def test_panel_packing_awards_points_and_repeated_click_does_not_duplicate(self):
        manager = get_user_model().objects.create_user('packing-manager', is_staff=True)
        self.client.force_login(manager)
        order = self.order()
        for _ in range(2):
            response = self.client.post(reverse('panel_section', args=['narudzbe']), {'action': 'pack', 'order_id': order.pk})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(LoyaltyCard.objects.get(customer=self.user).points, 90)
        self.assertEqual(order.point_entries.filter(kind='earn').count(), 1)
