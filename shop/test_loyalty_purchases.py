from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import LoyaltyCard, Order


@patch('shop.modules.loyalty_enabled', return_value=True)
class LoyaltyPurchasesTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.manager = users.objects.create_user('purchase-manager', is_staff=True)
        self.customer = users.objects.create_user('card-customer')
        self.card, _ = LoyaltyCard.objects.get_or_create(customer=self.customer)
        self.url = reverse('panel_section', args=['loyalty'])

    def test_card_info_and_purchase_total(self, enabled):
        for status, total in [('placed', 100), ('shipped', 200), ('completed', 300), ('cancelled', 400), ('draft', 500)]:
            Order.objects.bulk_create([Order(customer=self.customer, status=status, total=total, delivery_price=10)])
        other = get_user_model().objects.create_user('other-buyer')
        Order.objects.bulk_create([Order(customer=other, total=999)])
        self.client.force_login(self.manager)
        response = self.client.get(self.url)
        self.assertContains(response, f'data-card-purchases="{self.card.pk}"')
        response = self.client.get(self.url, {'card': self.card.pk})
        self.assertEqual(response.context['spent'], 570)
        self.assertEqual(len(response.context['purchases']), 4)
        self.assertContains(response, 'Otkazano')
        self.assertNotContains(response, '999')

    def test_purchase_details_require_staff_and_active_module(self, enabled):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(self.url, {'card': self.card.pk}).status_code, 302)
        self.client.force_login(self.manager)
        enabled.return_value = False
        self.assertEqual(self.client.get(self.url, {'card': self.card.pk}).status_code, 404)

    def test_invalid_card_returns_not_found(self, enabled):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url, {'card': 'invalid'}).status_code, 404)

    def test_search_by_customer_name_username_and_card(self, enabled):
        self.customer.first_name = 'Marko'
        self.customer.last_name = 'Markovic'
        self.customer.save()
        self.client.force_login(self.manager)
        for term in ['Marko', 'Marko Markovic', 'card-customer', self.card.number]:
            response = self.client.get(self.url, {'q': term})
            self.assertEqual(list(response.context['loyalty_cards']), [self.card])
        response = self.client.get(self.url, {'q': 'nepostojeci-kupac'})
        self.assertEqual(list(response.context['loyalty_cards']), [])
        self.assertContains(response, 'Nema kartica za unesenu pretragu.')

    def test_search_by_phone_returns_each_card_once(self, enabled):
        Order.objects.bulk_create([
            Order(customer=self.customer, phone='061123456'),
            Order(customer=self.customer, phone='061123456'),
        ])
        self.client.force_login(self.manager)
        for term in ['061123456', '123456']:
            response = self.client.get(self.url, {'q': term})
            self.assertEqual(list(response.context['loyalty_cards']), [self.card])
