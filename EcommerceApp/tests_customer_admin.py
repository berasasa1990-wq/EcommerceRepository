from decimal import Decimal
import re

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Coupon, UserProfile
from .emails import send_coupon_reward_email
from .pricing import izracunaj_sazetak


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class CustomerAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('staff@example.com', 'staff@example.com', 'secret12345')
        self.customer = User.objects.create_user('buyer@example.com', 'buyer@example.com', 'secret12345')
        self.customer.first_name = 'Kupac'
        self.customer.save(update_fields=['first_name'])
        UserProfile.objects.create(user=self.customer, telefon='061234567')
        self.client.force_login(self.admin)

    def test_user_admin_shows_contact_login_dates_and_coupons(self):
        response = self.client.get(reverse('admin:auth_user_change', args=[self.customer.pk]))
        self.assertEqual(response.status_code, 200)
        for value in ('buyer@example.com', '061234567', 'Prva zabilježena prijava', 'Posljednja prijava', 'Lični kuponi'):
            self.assertContains(response, value)
        self.assertContains(response, 'Pošalji email kupcu')

    def test_coupon_reward_email_explains_how_to_use_reward(self):
        coupon = Coupon.objects.create(
            kod='NAGRADA10', naziv='Nagrada', vlasnik=self.customer,
            vrsta=Coupon.Vrsta.IZNOS, iznos=Decimal('10'), postotak=0,
        )
        self.assertEqual(send_coupon_reward_email(coupon), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        self.assertIn('10 KM popusta', mail.outbox[0].subject)
        self.assertIn('Primijeni kupon ili karticu', mail.outbox[0].body)
        self.assertIn('/prijava/?next=/korpa/', mail.outbox[0].body)

    def test_reset_sends_to_selected_user_only_after_post(self):
        url = reverse('admin:customer_reset_password', args=[self.customer.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.customer.email])
        self.assertIn('/lozinka/potvrdi/', mail.outbox[0].body)
        link = re.search(r'https?://[^\s]+/lozinka/potvrdi/[^\s]+', mail.outbox[0].body).group(0)
        self.client.logout()
        response = self.client.get(link)
        self.assertEqual(response.status_code, 302)
        response = self.client.post(response['Location'], {
            'new_password1': 'new-secret-12345',
            'new_password2': 'new-secret-12345',
        })
        self.assertRedirects(response, '/lozinka/gotovo/')
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.check_password('new-secret-12345'))

    def test_first_login_is_recorded(self):
        self.client.force_login(self.customer)
        self.assertIsNotNone(UserProfile.objects.get(user=self.customer).prva_prijava)

    def test_personal_fixed_coupon_changes_total(self):
        coupon = Coupon.objects.create(kod='KUPAC10', naziv='Poklon', vlasnik=self.customer,
                                       vrsta=Coupon.Vrsta.IZNOS, iznos=Decimal('10'), postotak=0)
        summary = izracunaj_sazetak(Decimal('100'), user=self.customer, coupon_code=coupon.kod)
        self.assertEqual(summary['kupon_popust'], Decimal('10'))

    def test_personal_free_shipping_coupon_changes_delivery(self):
        coupon = Coupon.objects.create(kod='DOSTAVA1', naziv='Dostava', vlasnik=self.customer,
                                       vrsta=Coupon.Vrsta.DOSTAVA, postotak=0)
        summary = izracunaj_sazetak(Decimal('10'), user=self.customer, coupon_code=coupon.kod)
        self.assertEqual(summary['dostava'], Decimal('0'))
