from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import LoyaltyRules, LoyaltyTier, LoyaltyCard, Order, Product, SiteModule
from .loyalty import loyalty_status, point_quote, redeem_points


class LoyaltyFeaturesTests(TestCase):
    def setUp(self):
        SiteModule.objects.update_or_create(code='loyalty', defaults={'enabled': True})
        self.manager = get_user_model().objects.create_user('feature-manager', is_staff=True)
        self.buyer = get_user_model().objects.create_user('feature-buyer')
        self.url = reverse('panel_section', args=['loyalty'])
        self.config = LoyaltyRules.objects.create()

    def test_tabs_and_independent_settings(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.url)
        self.assertContains(response, 'Pretraga kartice')
        self.assertNotContains(response, 'id="loyaltyRows"')
        response = self.client.get(self.url, {'tab': 'settings'})
        self.assertContains(response, 'Nivoi kartice')
        self.assertContains(response, 'Bodovi')
        self.assertNotContains(response, 'id="loyaltyCardSearch"')
        self.client.post(self.url, {'settings_section': 'features', 'points_enabled': 'on'})
        self.config.refresh_from_db()
        self.assertFalse(self.config.tiers_enabled)
        self.assertTrue(self.config.points_enabled)
        self.client.post(self.url, {'settings_section': 'features', 'tiers_enabled': 'on'})
        self.config.refresh_from_db()
        self.assertTrue(self.config.tiers_enabled)
        self.assertFalse(self.config.points_enabled)

    def test_disabled_tiers_keep_spend_but_remove_discount(self):
        Order.objects.create(customer=self.buyer, total=100)
        LoyaltyTier.objects.create(minimum=0, percent=10)
        self.config.tiers_enabled = False
        self.config.save()
        self.assertEqual(loyalty_status(self.buyer), (Decimal('100'), Decimal('0')))

    def test_disabled_points_block_earning_and_spending_but_preserve_refunds(self):
        card, _ = LoyaltyCard.objects.get_or_create(customer=self.buyer)
        card.points = 200
        card.save()
        order = Order.objects.create(customer=self.buyer, total=100)
        redeem_points(order, self.buyer, Decimal('100'), 100)
        self.config.points_enabled = False
        self.config.save()
        self.assertEqual(point_quote(self.buyer, Decimal('100'), 50), (0, Decimal('0')))
        Order.objects.create(customer=self.buyer, total=100, status='completed')
        card.refresh_from_db()
        self.assertEqual(card.points, 100)
        order.status = 'cancelled'
        order.save()
        card.refresh_from_db()
        self.assertEqual(card.points, 200)

    def test_disabled_features_hide_customer_controls(self):
        self.config.points_enabled = False
        self.config.tiers_enabled = False
        self.config.save()
        self.client.force_login(self.buyer)
        response = self.client.get(reverse('loyalty'))
        self.assertNotContains(response, 'Historija bodova')
        self.assertNotContains(response, 'Nivoi koji prate')
        product = Product.objects.create(name='Test', price=10)
        session = self.client.session
        session['cart'] = {str(product.pk): 1}
        session.save()
        self.assertNotContains(self.client.get(reverse('checkout')), 'id="loyalty-points"')

    def test_point_ratios_save_and_apply_to_purchase(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.url, {
            'settings_section': 'points', 'award_spend': '10', 'points_per_km': '2',
            'redemption_points': '100', 'point_value': '5', 'max_discount_percent': '50',
        })
        self.assertRedirects(response, self.url + '?tab=settings')
        Order.objects.create(customer=self.buyer, total=110, delivery_price=10, status='completed')
        card = LoyaltyCard.objects.get(customer=self.buyer)
        self.assertEqual(card.points, 20)
        self.assertEqual(point_quote(self.buyer, Decimal('100'), 20), (20, Decimal('1.00')))
        self.client.post(self.url, {'settings_section': 'features', 'tiers_enabled': 'on', 'points_enabled': 'on'})
        self.config.refresh_from_db()
        self.assertEqual(self.config.point_value, Decimal('5'))
        self.assertEqual(self.config.redemption_points, 100)

    def test_sections_start_closed_and_invalid_points_stay_open(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.url, {'tab': 'settings'})
        self.assertContains(response, '<details><summary>Loyalty popusti</summary>')
        self.assertContains(response, '<details><summary>Bodovi</summary>')
        response = self.client.post(self.url, {
            'settings_section': 'points', 'award_spend': '0', 'points_per_km': '1',
            'redemption_points': '0', 'point_value': '0', 'max_discount_percent': '101',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<details open><summary>Bodovi</summary>')
        self.config.refresh_from_db()
        self.assertEqual(self.config.award_spend, Decimal('1'))
