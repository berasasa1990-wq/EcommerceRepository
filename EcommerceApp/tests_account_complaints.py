from unittest.mock import patch
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from .models import Order, OrderItem


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test', EMAIL_HOST_PASSWORD='test',
    ORDER_NOTIFICATION_EMAIL='shop@example.com',
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
)
class AccountComplaintTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.customer = User.objects.create_user('complaint-customer', email='customer@example.com', password='test1234')
        self.other = User.objects.create_user('complaint-other', email='other@example.com')
        self.order = self.make_order(self.customer, 'COMPLAINT-1')
        self.other_order = self.make_order(self.other, 'PRIVATE-2')
        self.item = self.make_item(self.order, 'Štap', 'STAP-1')
        self.second = self.make_item(self.order, 'Mašinica', 'MAS-2')
        self.other_item = self.make_item(self.other_order, 'Privatni artikal', 'PRIV-3')
        self.client.force_login(self.customer)
        self.url = reverse('account')
        self.data = {'action': 'reklamacija', 'complaint_order': self.order.broj,
                     'item_id': self.item.pk, 'opis': 'Vrh štapa je oštećen prilikom dostave.'}

    def make_order(self, user, number):
        return Order.objects.create(korisnik=user, broj=number, ime_prezime='Kupac', email=user.email,
                                    telefon='061123456', adresa='Ulica 1', grad='Sarajevo', ukupno=100)

    def make_item(self, order, name, code):
        return OrderItem.objects.create(narudzba=order, naziv=name, product_naziv=name, sifra=code,
                                        varijacija_naziv='Model A', cijena=50, kolicina=1)

    def test_select_order_displays_every_item_and_only_owned_orders(self):
        response = self.client.get(self.url, {'complaint_order': self.order.broj})
        self.assertContains(response, 'Štap — Model A')
        self.assertContains(response, 'Mašinica — Model A')
        self.assertContains(response, 'name="item_id"', count=2)
        self.assertNotContains(response, 'PRIVATE-2')
        self.assertEqual(response.context['account_initial_section'], 'reklamacije')
        self.assertContains(response, 'account-complaints.js')

    def test_submission_sends_email_to_shop_with_reply_to_customer(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        self.assertIn('#reklamacije', response['Location'])
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['shop@example.com'])
        self.assertEqual(message.reply_to, ['customer@example.com'])
        for text in ['COMPLAINT-1', 'Štap — Model A', 'STAP-1', self.data['opis'], 'customer@example.com']:
            self.assertIn(text, message.body)
        self.assertNotIn('Mašinica', message.body)
        self.client.get(response['Location'])
        self.assertEqual(len(mail.outbox), 1)

    def test_cannot_view_or_submit_another_customers_order(self):
        self.assertEqual(self.client.get(self.url, {'complaint_order': self.other_order.broj}).status_code, 404)
        response = self.client.post(self.url, {**self.data, 'complaint_order': self.other_order.broj, 'item_id': self.other_item.pk})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_item_must_belong_to_selected_order(self):
        response = self.client.post(self.url, {**self.data, 'item_id': self.other_item.pk})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_blank_and_oversized_descriptions_are_rejected(self):
        for problem in ['   ', 'x' * 5001]:
            response = self.client.post(self.url, {**self.data, 'opis': problem})
            self.assertEqual(response.status_code, 200)
            item = list(response.context['complaint_order'].stavke.all())[0]
            self.assertIn('opis', item.complaint_form.errors)
            self.assertTrue(item.complaint_open)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_failure_keeps_description_and_does_not_report_success(self):
        with patch('EcommerceApp.emails.send_order_complaint', side_effect=RuntimeError('SMTP unavailable')):
            response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data['opis'])
        self.assertContains(response, 'Slanje trenutno nije uspjelo')
        self.assertNotContains(response, 'Reklamacija je poslana.')
        self.assertEqual(len(mail.outbox), 0)

    def test_login_and_csrf_are_required(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.customer)
        self.assertEqual(strict.post(self.url, self.data).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)
