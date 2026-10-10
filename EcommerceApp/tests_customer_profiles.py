from unittest.mock import patch
import re
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import UserProfile, Order, LoyaltyCard, Coupon


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CustomerProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = get_user_model().objects.create_user('profile-staff', password='staff-password', is_staff=True)
        self.customer = get_user_model().objects.create_user('profile-customer', email='customer@example.invalid',
            password='customer-password', first_name='Demo', last_name='Kupac')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.customer)
        self.profile.telefon = '+38760111222'
        self.profile.adresa = 'Adresa 12'
        self.profile.save()
        self.client.force_login(self.staff)
        self.url = reverse('panel_admin:EcommerceApp_userprofile_change', args=[self.profile.pk])
        self.reset_url = reverse('panel_admin:profile_reset_password', args=[self.profile.pk])

    def test_overview_shows_account_orders_loyalty_and_coupons(self):
        order = Order.objects.create(korisnik=self.customer, ime_prezime='Demo Kupac', email=self.customer.email,
            telefon='123', adresa='Adresa 12', grad='Sarajevo', ukupno=25)
        LoyaltyCard.objects.create(user=self.customer, kod='DEMO-CARD', barkod='DEMO-BARCODE')
        Coupon.objects.create(vlasnik=self.customer, kod='DEMO-COUPON', naziv='Demo kupon')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for text in ('Demo Kupac', self.customer.email, '+38760111222', 'Adresa 12', order.broj,
                     'DEMO-CARD', 'DEMO-COUPON', 'Posljednja prijava', self.reset_url):
            self.assertContains(response, text)
        self.assertNotContains(response, self.customer.password)

    def test_reset_requires_post_and_sends_valid_customer_link(self):
        password = self.customer.password
        self.assertEqual(self.client.get(self.reset_url).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        response = self.client.post(self.reset_url, {})
        self.assertRedirects(response, self.url)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        uid = urlsafe_base64_encode(force_bytes(self.customer.pk))
        token = re.search(r'/lozinka/potvrdi/' + uid + r'/([^/\s]+)/', mail.outbox[0].body)
        self.assertIsNotNone(token)
        self.assertTrue(default_token_generator.check_token(self.customer, token.group(1)))
        self.assertIn('http://testserver' + reverse('password_reset_confirm', args=[uid, token.group(1)]), mail.outbox[0].body)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.password, password)

    def test_reset_denies_customers_superuser_targets_and_missing_csrf(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(self.reset_url, {}).status_code, 302)
        self.client.force_login(self.staff)
        owner = get_user_model().objects.create_superuser('profile-owner', 'owner@example.invalid', 'owner-password')
        profile, _ = UserProfile.objects.get_or_create(user=owner)
        self.assertEqual(self.client.post(reverse('panel_admin:profile_reset_password', args=[profile.pk]), {}).status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(self.reset_url, {}).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_reports_error_and_keeps_password(self):
        with patch('EcommerceApp.customer_profiles.send_mail', side_effect=RuntimeError('mail unavailable')):
            response = self.client.post(self.reset_url, {}, follow=True)
        self.assertContains(response, 'Email nije poslan.')
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.check_password('customer-password'))

    def test_inactive_account_does_not_send(self):
        self.customer.is_active = False
        self.customer.save(update_fields=['is_active'])
        self.client.post(self.reset_url, {})
        self.assertEqual(len(mail.outbox), 0)
