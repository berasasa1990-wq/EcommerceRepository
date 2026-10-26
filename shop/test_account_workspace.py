from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from .models import Order, Product, OrderItem


class AccountWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('customer-workspace', 'buyer@example.com', 'Old!River-Stone-2934')
        self.client.force_login(self.user)

    def test_password_requires_current_password_and_keeps_session(self):
        payload = {'action': 'password', 'old_password': 'wrong', 'new_password1': 'New!River-Stone-2934', 'new_password2': 'New!River-Stone-2934'}
        response = self.client.post(reverse('account'), payload)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Old!River-Stone-2934'))
        payload['old_password'] = 'Old!River-Stone-2934'
        response = self.client.post(reverse('account'), payload)
        self.assertRedirects(response, reverse('account')+'?tab=sigurnost')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New!River-Stone-2934'))
        self.assertEqual(self.client.get(reverse('account')).status_code, 200)

    def test_private_paginated_orders_and_delivery(self):
        other = get_user_model().objects.create_user('other-workspace')
        hidden = Order.objects.create(customer=other, customer_name='PRIVATE OTHER', address='PRIVATE ADDRESS', total=999)
        for _ in range(12):
            Order.objects.create(customer=self.user, customer_name='My name', address='My street', status='placed')
        response = self.client.get(reverse('account'))
        self.assertEqual(len(response.context['orders']), 10)
        self.assertEqual(response.context['account_stats']['count'], 12)
        self.assertContains(response, 'My street')
        self.assertNotContains(response, 'PRIVATE OTHER')
        self.assertNotContains(response, 'PRIVATE ADDRESS')
        self.assertEqual(len(self.client.get(reverse('account'), {'page': 2}).context['orders']), 2)
        self.assertEqual(self.client.get(reverse('account'), {'page': 'bad'}).status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])

    def test_security_csrf_and_profile_fields(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(reverse('account'), {'action': 'password'}).status_code, 403)
        self.client.post(reverse('account'), {'phone': '061123456', 'first_name': 'Kupac', 'last_name': 'Test', 'email': 'new@example.com', 'is_staff': True})
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Kupac')
        self.assertFalse(self.user.is_staff)
        self.assertContains(self.client.get(reverse('account'), {'tab': 'sigurnost'}), 'Trenutna lozinka')
