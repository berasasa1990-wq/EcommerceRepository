import hashlib
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .models import Order, CardPayment
from .forms import CheckoutForm
from .monri import form_data
from .magacin import validate_order_stock, MagacinError
from .xexpress_service import create_shipment, XExpressError


@override_settings(ALLOWED_HOSTS=['testserver'], MONRI_ENABLED=True, MONRI_ENVIRONMENT='test',
    MONRI_MERCHANT_KEY='private-test-key', MONRI_AUTHENTICITY_TOKEN='public-test-token',
    MONRI_PUBLIC_BASE_URL='https://carpologijabh.ba',
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class MonriTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(ime_prezime='Test Kupac', email='test@example.com', telefon='061123456',
            adresa='Ulica 1', grad='Sarajevo', ukupno=Decimal('25.50'))
        self.payment = CardPayment.objects.create(order=self.order, amount=2550)

    def payload(self, **changes):
        return {'id': 123, 'order_number': self.order.broj, 'amount': 2550, 'currency': 'BAM',
                'status': 'approved', 'response_code': '0000', 'transaction_type': 'purchase', **changes}

    def callback(self, payload, valid=True):
        body = json.dumps(payload)
        signature = hashlib.sha512(('private-test-key' + body).encode()).hexdigest()
        return self.client.post(reverse('monri_callback'), body, content_type='application/json',
            HTTP_AUTHORIZATION='WP3-callback ' + (signature if valid else 'bad'))

    def test_form_uses_server_amount_test_endpoint_and_never_exposes_key(self):
        endpoint, fields = form_data(self.payment)
        self.assertEqual(endpoint, 'https://ipgtest.monri.com/v2/form')
        self.assertEqual(fields['amount'], '2550')
        self.assertEqual(fields['currency'], 'BAM')
        expected = hashlib.sha512(('private-test-key' + self.order.broj + '2550BAM').encode()).hexdigest()
        self.assertEqual(fields['digest'], expected)
        response = self.client.get(reverse('monri_start', args=[self.payment.token]))
        self.assertContains(response, 'Nastavi na plaćanje')
        self.assertContains(response, 'Testno plaćanje')
        self.assertNotContains(response, 'private-test-key')
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_verified_callback_pays_once_and_preserves_amount(self):
        self.assertEqual(self.callback(self.payload()).status_code, 200)
        self.payment.refresh_from_db()
        timestamp = self.payment.paid_at
        self.assertEqual(self.payment.status, 'paid')
        self.assertTrue(Order.objects.get(pk=self.order.pk).placeno_karticom())
        self.assertEqual(self.callback(self.payload()).status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.paid_at, timestamp)
        self.assertEqual(self.callback(self.payload(id=999)).status_code, 409)

    def test_official_digest_example(self):
        # Public documentation vector, unrelated to any merchant configuration.
        self.order.broj = 'abcdef'
        self.payment.amount = 54321
        self.payment.currency = 'EUR'
        with override_settings(MONRI_MERCHANT_KEY='2345klj'):
            _, fields = form_data(self.payment)
        self.assertTrue(fields['digest'] == (
            'f71b8c1560bd7511ba2f0307b3823c06dd39042cd77480543e3d7bf9f3eefa6'
            'debed252979ba8edc7a82d9f111d90f8e31c1c7ab5af39796b26e59a0b2d7cf98'))

    def test_rendered_post_preserves_exact_signed_values_and_token(self):
        from html.parser import HTMLParser
        from urllib.parse import urlencode, parse_qsl
        from secrets import token_hex
        class FormParser(HTMLParser):
            fields = None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'form':
                    self.in_monri = attrs.get('action') == 'https://ipgtest.monri.com/v2/form'
                    if self.in_monri:
                        self.fields = {}
                        self.method = attrs.get('method')
                elif tag == 'input' and getattr(self, 'in_monri', False):
                    self.fields[attrs['name']] = attrs.get('value', '')
            def handle_endtag(self, tag):
                if tag == 'form':
                    self.in_monri = False
        # Synthetic edge fixtures exercise HTML escaping, UTF-8 and leading zeroes.
        key, token = token_hex(32) + 'č', token_hex(20) + '&"<+'
        self.order.broj = '00562'
        self.order.save(update_fields=['broj'])
        self.payment.amount = 1300
        self.payment.save(update_fields=['amount'])
        with override_settings(MONRI_MERCHANT_KEY=key, MONRI_AUTHENTICITY_TOKEN=token, LANGUAGE_CODE='bs'):
            parser = FormParser()
            parser.feed(self.client.get(reverse('monri_start', args=[self.payment.token])).content.decode('utf-8'))
        posted = dict(parse_qsl(urlencode(parser.fields), keep_blank_values=True))
        self.assertEqual(parser.method, 'post')
        self.assertTrue(posted['authenticity_token'] == token)
        self.assertEqual(posted['order_number'], '00562')
        self.assertEqual(posted['amount'], '1300')
        self.assertEqual(posted['currency'], 'BAM')
        expected = hashlib.sha512((key + posted['order_number'] + posted['amount'] + posted['currency']).encode('utf-8')).hexdigest()
        self.assertTrue(posted['digest'] == expected)
        self.assertEqual(set(posted), {'authenticity_token', 'order_number', 'amount', 'currency',
            'ch_full_name', 'ch_email', 'ch_address', 'ch_city', 'ch_zip', 'ch_country', 'ch_phone',
            'order_info', 'transaction_type', 'language', 'success_url_override',
            'cancel_url_override', 'callback_url_override', 'digest'})
        for field, route, args in (
            ('success_url_override', 'monri_return', [self.payment.token]),
            ('cancel_url_override', 'monri_cancel', [self.payment.token]),
            ('callback_url_override', 'monri_callback', [])):
            self.assertEqual(posted[field], 'https://carpologijabh.ba' + reverse(route, args=args))

    def test_diagnostic_log_has_only_allowlisted_metadata(self):
        from secrets import token_hex
        key, token = token_hex(32), token_hex(20)
        with override_settings(MONRI_MERCHANT_KEY=key, MONRI_AUTHENTICITY_TOKEN=token):
            with self.assertLogs('EcommerceApp.monri', level='INFO') as captured:
                endpoint, fields = form_data(self.payment)
        output = '\n'.join(captured.output)
        for forbidden in (key, token, fields['digest'], self.order.email, self.order.ime_prezime, self.order.telefon):
            self.assertTrue(forbidden not in output)
        for metadata in (endpoint, 'amount=2550', 'currency=BAM', 'merchant_key_length=64',
                         'authenticity_token_length=40', 'digest_algorithm=SHA-512', 'digest_encoding=UTF-8'):
            self.assertIn(metadata, output)
        with override_settings(MONRI_ENVIRONMENT='invalid'):
            with self.assertNoLogs('EcommerceApp.monri'):
                with self.assertRaises(ValueError):
                    form_data(self.payment)

    def test_forged_wrong_amount_currency_and_declined_do_not_pay(self):
        self.assertEqual(self.callback(self.payload(), valid=False).status_code, 403)
        for changes in ({'amount': 1}, {'currency': 'EUR'}, {'status': 'declined'},
                        {'response_code': '1000'}, {'transaction_type': 'authorize'}, {'amount': '2550'}):
            self.assertEqual(self.callback(self.payload(**changes)).status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')

    def test_redirect_and_cancel_never_mark_paid(self):
        for route in ('monri_return', 'monri_cancel'):
            self.client.get(reverse(route, args=[self.payment.token]), {'status': 'approved', 'response_code': '0000'})
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')
        self.assertFalse(Order.objects.get(pk=self.order.pk).placeno_karticom())

    def test_paid_return_redirects_to_visible_order_success(self):
        self.assertEqual(self.callback(self.payload()).status_code, 200)
        response = self.client.get(reverse('monri_return', args=[self.payment.token]))
        self.assertRedirects(response, reverse('order_success', args=[self.order.broj]))
        success = self.client.get(response.url)
        self.assertContains(success, 'Plaćanje uspješno')
        self.assertContains(success, 'KARTICOM')
        self.assertNotContains(success, 'Nastavi plaćanje')

    def test_pending_return_auto_checks_without_offering_payment_retry(self):
        response = self.client.get(reverse('monri_return', args=[self.payment.token]),
            {'status': 'approved', 'approval_code': 'FAKE', 'amount': 2550, 'response_code': '0000'})
        self.assertContains(response, 'Plaćanje se potvrđuje...')
        self.assertContains(response, 'monri-payment-status.js')
        self.assertContains(response, reverse('monri_status', args=[self.payment.token]))
        self.assertContains(response, 'nemojte ponavljati plaćanje')
        self.assertNotContains(response, 'Nastavi plaćanje')
        self.assertNotContains(response, reverse('monri_start', args=[self.payment.token]) + '"')
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')

    def test_status_endpoint_reads_only_callback_verified_state(self):
        url = reverse('monri_status', args=[self.payment.token])
        response = self.client.get(url, {'status': 'paid', 'approval_code': 'FAKE'})
        self.assertEqual(response.json(), {'status': 'pending', 'success_url': None})
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(self.client.post(url, {'status': 'paid'}).status_code, 405)
        self.assertEqual(self.callback(self.payload(), valid=False).status_code, 403)
        self.assertEqual(self.client.get(url).json()['status'], 'pending')
        self.assertEqual(self.callback(self.payload()).status_code, 200)
        self.assertEqual(self.client.get(url).json(), {'status': 'paid',
            'success_url': reverse('order_success', args=[self.order.broj])})

    def test_paid_payment_cannot_generate_or_post_a_new_monri_form(self):
        self.callback(self.payload())
        self.payment.refresh_from_db()
        with self.assertRaises(ValueError):
            form_data(self.payment)
        url = reverse('monri_start', args=[self.payment.token])
        response = self.client.get(url)
        self.assertRedirects(response, reverse('order_success', args=[self.order.broj]), fetch_redirect_response=False)
        self.assertNotIn(b'ipg.monri.com/v2/form', response.content)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_pending_card_cannot_be_fulfilled_or_sent_to_courier(self):
        with self.assertRaises(MagacinError):
            validate_order_stock(self.order)
        with patch('EcommerceApp.xexpress_service.requests.post') as http:
            with self.assertRaises(XExpressError):
                create_shipment(self.order)
            http.assert_not_called()

    def test_card_record_overrides_unverified_note_and_amount_changes(self):
        self.order.napomena = 'plaćeno karticom'
        self.assertFalse(self.order.placeno_karticom())
        self.callback(self.payload())
        self.order = Order.objects.get(pk=self.order.pk)
        self.order.ukupno = Decimal('99')
        with self.assertRaises(XExpressError):
            create_shipment(self.order)

    def test_card_unavailable_without_complete_configuration_and_requires_email(self):
        data = dict(ime_prezime='Test Kupac', telefon='061123456', adresa='Ulica 1', grad='Sarajevo', payment_method='card')
        form = CheckoutForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('payment_method', form.errors)
        with override_settings(MONRI_ENABLED=False):
            form = CheckoutForm({**data, 'email': 'test@example.com'})
            self.assertFalse(form.is_valid())
            self.assertIn('card', dict(form.fields['payment_method'].choices))
            self.assertFalse(form.card_payment_available)
            self.assertIn('trenutno nije dostupno', form.errors['payment_method'][0])
            self.assertIn('Karticom', str(form['payment_method']))
            self.assertEqual(self.client.get(reverse('monri_start', args=[self.payment.token])).status_code, 503)
        data.pop('payment_method')
        form = CheckoutForm(data)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['payment_method'], 'cod')

    def test_callback_finalizes_only_once_after_commit(self):
        with patch('EcommerceApp.views_monri.finish_paid_order') as finish:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.callback(self.payload()).status_code, 200)
            finish.assert_called_once()
            CardPayment.objects.filter(pk=self.payment.pk).update(finalized_at=timezone.now())
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.callback(self.payload()).status_code, 200)
            finish.assert_called_once()

    def test_checkout_card_creates_pending_payment_and_defers_purchase_side_effects(self):
        from contextlib import ExitStack
        from .models import Product
        product = Product.objects.create(naziv='Artikal', cijena=10, stanje=5, aktivan=True, na_stanju=True)
        session = self.client.session
        session['cart'] = {f'{product.pk}:0': {'product_id': product.pk, 'variation_id': None,
            'quantity': 1, 'cijena': '10.00', 'bazna_cijena': '10.00', 'na_akciji': False,
            'naziv': product.naziv, 'product_naziv': product.naziv, 'sifra': 'TEST-SKU'}}
        session.save()
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(patch('EcommerceApp.views.' + name)) for name in
                     ('queue_order_emails', 'sync_narudzba', 'track_purchase', 'azuriraj_loyalty_nakon_narudzbe')}
            notify = stack.enter_context(patch('EcommerceApp.staff_alerts.notify_purchase'))
            response = self.client.get(reverse('checkout'))
            self.assertContains(response, 'Karticom')
            response = self.client.post(reverse('checkout'), dict(ime_prezime='Test Kupac', telefon='061123456',
                email='test@example.com', adresa='Ulica 1', grad='Sarajevo', payment_method='card'))
            for mock in mocks.values():
                mock.assert_not_called()
            notify.assert_not_called()
        payment = CardPayment.objects.exclude(pk=self.payment.pk).get()
        self.assertEqual(payment.status, 'pending')
        self.assertIsNone(payment.order_id)
        self.assertEqual(Order.objects.count(), 1)  # Only the setUp fixture exists.
        from .monri import checkout_order
        self.assertEqual(payment.amount, int(checkout_order(payment).ukupno * 100))
        self.assertRedirects(response, reverse('monri_start', args=[payment.token]), fetch_redirect_response=False)
        self.assertFalse(checkout_order(payment).placeno_karticom())

    def test_card_draft_is_accepted_only_by_verified_callback(self):
        self.order.status = Order.Status.CEKA_PLACANJE
        self.order.save(update_fields=['status'])
        self.assertEqual(self.order.web_placanje_label(), 'KARTICOM')
        self.assertRedirects(self.client.get(reverse('order_success', args=[self.order.broj])),
            reverse('monri_return', args=[self.payment.token]), fetch_redirect_response=False)
        self.test_redirect_and_cancel_never_mark_paid()
        self.test_forged_wrong_amount_currency_and_declined_do_not_pay()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CEKA_PLACANJE)
        with patch('EcommerceApp.views_monri.finish_paid_order') as finish:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.callback(self.payload()).status_code, 200)
            self.order.refresh_from_db()
            accepted_at = self.order.kreirana
            self.assertEqual(self.order.status, Order.Status.NOVA)
            self.assertTrue(self.order.placeno_karticom())
            CardPayment.objects.filter(pk=self.payment.pk).update(finalized_at=timezone.now())
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.callback(self.payload()).status_code, 200)
            finish.assert_called_once()
            self.order.refresh_from_db()
            self.assertEqual(self.order.kreirana, accepted_at)

    def test_card_draft_hidden_from_received_orders_until_paid(self):
        from django.contrib.auth import get_user_model
        from .views import _search_staff_orders
        user = get_user_model().objects.create_superuser(username='monri-staff', email='staff@example.com', password='fixture-only')
        self.order.status = Order.Status.CEKA_PLACANJE
        self.order.save(update_fields=['status'])
        self.assertFalse(_search_staff_orders(self.order.broj).exists())
        self.client.force_login(user)
        response = self.client.get(reverse('staff_online_orders'), {'filter': 'sve'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.order.pk, [order.pk for order in response.context['orders']])
        self.callback(self.payload())
        response = self.client.get(reverse('staff_online_orders'), {'filter': 'sve'})
        self.assertIn(self.order.pk, [order.pk for order in response.context['orders']])
        self.assertContains(response, 'KARTICOM')

    def test_cancelled_card_order_cannot_be_accepted_by_callback(self):
        self.order.status = Order.Status.OTKAZANA
        self.order.save(update_fields=['status'])
        self.assertEqual(self.callback(self.payload()).status_code, 409)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')

    def test_paid_card_has_no_cash_on_delivery_and_cancelled_order_cannot_ship(self):
        from .xexpress_service import order_is_pouzece
        self.assertEqual(self.callback(self.payload()).status_code, 200)
        order = Order.objects.get(pk=self.order.pk)
        self.assertFalse(order_is_pouzece(order))
        self.assertEqual(order.packing_placanje_label(), 'KARTICA')
        order.status = Order.Status.OTKAZANA
        with self.assertRaises(XExpressError):
            create_shipment(order)

    def test_callback_requires_post_and_unknown_order_is_rejected(self):
        self.assertEqual(self.client.get(reverse('monri_callback')).status_code, 405)
        self.assertEqual(self.callback(self.payload(order_number='UNKNOWN')).status_code, 404)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')

    def test_production_form_and_verified_callback_preserve_security(self):
        from .monri import configured
        self.payment.environment = 'production'
        self.payment.save(update_fields=['environment'])
        with override_settings(MONRI_ENVIRONMENT='production'):
            self.assertTrue(configured())
            with self.assertLogs('EcommerceApp.monri', level='INFO') as captured:
                endpoint, fields = form_data(self.payment)
            self.assertEqual(endpoint, 'https://ipg.monri.com/v2/form')
            output = '\n'.join(captured.output)
            for forbidden in ('private-test-key', 'public-test-token', fields['digest'], self.order.email):
                self.assertTrue(forbidden not in output)
            for field in ('success_url_override', 'cancel_url_override', 'callback_url_override'):
                self.assertTrue(fields[field].startswith('https://carpologijabh.ba/'))
            response = self.client.get(reverse('monri_start', args=[self.payment.token]))
            self.assertContains(response, 'https://ipg.monri.com/v2/form')
            self.assertNotContains(response, 'Testno plaćanje')
            self.test_redirect_and_cancel_never_mark_paid()
            self.test_forged_wrong_amount_currency_and_declined_do_not_pay()
            self.test_callback_finalizes_only_once_after_commit()
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.status, 'paid')

    @override_settings(MONRI_ENVIRONMENT='production')
    def test_production_checkout_creates_production_payment(self):
        self.test_checkout_card_creates_pending_payment_and_defers_purchase_side_effects()
        payment = CardPayment.objects.exclude(pk=self.payment.pk).get()
        self.assertEqual(payment.environment, 'production')

    def test_checkout_without_order_creates_one_order_only_after_signed_payment(self):
        self.test_checkout_card_creates_pending_payment_and_defers_purchase_side_effects()
        payment = CardPayment.objects.exclude(pk=self.payment.pk).get()
        for route in ('monri_return', 'monri_cancel', 'monri_start'):
            self.assertEqual(self.client.get(reverse(route, args=[payment.token])).status_code, 200)
        payload = self.payload(order_number=payment.reference, amount=payment.amount)
        self.assertEqual(self.callback(payload, valid=False).status_code, 403)
        self.assertEqual(self.callback({**payload, 'status': 'declined'}).status_code, 400)
        self.assertEqual(Order.objects.count(), 1)
        with patch('EcommerceApp.views_monri.finish_paid_order') as finish:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.callback(payload).status_code, 200)
            payment.refresh_from_db()
            self.assertIsNotNone(payment.order_id)
            self.assertEqual(payment.order.status, Order.Status.NOVA)
            self.assertEqual(payment.order.stavke.count(), 1)
            self.assertEqual(payment.order.web_placanje_label(), 'KARTICOM')
            self.assertEqual(Order.objects.count(), 2)
            CardPayment.objects.filter(pk=payment.pk).update(finalized_at=timezone.now())
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.callback(payload).status_code, 200)
            self.assertEqual(Order.objects.count(), 2)
            finish.assert_called_once()

    @override_settings(MONRI_ENVIRONMENT='production')
    def test_production_mode_preserves_cod_and_explicit_disable(self):
        self.test_card_unavailable_without_complete_configuration_and_requires_email()

    def test_environment_mismatch_and_unknown_environment_are_rejected(self):
        from .monri import configured
        for mode in ('production', 'invalid'):
            with override_settings(MONRI_ENVIRONMENT=mode):
                with self.assertRaises(ValueError):
                    form_data(self.payment)
                self.assertIn(self.callback(self.payload()).status_code, (400, 403))
        with override_settings(MONRI_ENVIRONMENT='invalid'):
            self.assertFalse(configured())
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')
        with override_settings(MONRI_ENVIRONMENT='production', MONRI_PUBLIC_BASE_URL='https://example.com'):
            self.assertFalse(configured())

    def test_missing_credentials_invalid_url_and_explicit_disable_block_only_card(self):
        from .monri import configured
        for config in ({'MONRI_MERCHANT_KEY': ''}, {'MONRI_AUTHENTICITY_TOKEN': ''},
                       {'MONRI_ENABLED': False}, {'MONRI_ENVIRONMENT': 'invalid'},
                       {'MONRI_PUBLIC_BASE_URL': 'http://carpologijabh.ba'},
                       {'MONRI_PUBLIC_BASE_URL': 'https://example.com/path'},
                       {'MONRI_PUBLIC_BASE_URL': 'https://example.com:invalid'},
                       {'MONRI_PUBLIC_BASE_URL': 'https:// bad.example.com'}):
            with self.subTest(flags=list(config)), override_settings(**config):
                self.assertFalse(configured())
                form = CheckoutForm(dict(ime_prezime='Test Kupac', telefon='061123456',
                    adresa='Ulica 1', grad='Sarajevo', payment_method='cod'))
                self.assertTrue(form.is_valid())
                self.assertEqual(form.cleaned_data['payment_method'], 'cod')


class MonriConfigurationTests(SimpleTestCase):
    def test_render_credentials_alone_enable_test_and_process_environment_wins(self):
        from EcommerceProject.monri_config import read_monri_config
        # Generated fixtures only; never read real merchant keys in tests.
        from secrets import token_hex
        key, token = token_hex(32), token_hex(20)
        config = read_monri_config({'MONRI_MERCHANT_KEY': key, 'MONRI_AUTHENTICITY_TOKEN': token},
                                  {'MONRI_MERCHANT_KEY': '', 'MONRI_AUTHENTICITY_TOKEN': '', 'MONRI_ENVIRONMENT': 'test'})
        self.assertTrue(config['MONRI_ENABLED'])
        self.assertEqual(config['MONRI_ENVIRONMENT'], 'test')
        self.assertTrue(config['MONRI_MERCHANT_KEY'] == key)
        self.assertTrue(config['MONRI_AUTHENTICITY_TOKEN'] == token)
        self.assertEqual(config['MONRI_PUBLIC_BASE_URL'], 'https://carpologijabh.ba')

    def test_disable_unknown_mode_and_empty_environment_fail_closed(self):
        from EcommerceProject.monri_config import read_monri_config
        self.assertFalse(read_monri_config({'MONRI_ENABLED': 'False'}, {})['MONRI_ENABLED'])
        self.assertTrue(read_monri_config({'MONRI_ENVIRONMENT': 'production'}, {})['MONRI_ENABLED'])
        self.assertFalse(read_monri_config({'MONRI_ENVIRONMENT': 'invalid'}, {})['MONRI_ENABLED'])
        self.assertTrue(read_monri_config({'MONRI_ENVIRONMENT': ' TEST '}, {})['MONRI_ENABLED'])
        self.assertFalse(bool(read_monri_config({'MONRI_MERCHANT_KEY': ''}, {'MONRI_MERCHANT_KEY': 'dummy'})['MONRI_MERCHANT_KEY']))

    def test_django_settings_read_process_credentials_before_dotenv_override(self):
        import os
        import subprocess
        import sys
        from secrets import token_hex
        environment = {name: value for name, value in os.environ.items() if not name.startswith('MONRI_')}
        environment.update(MONRI_MERCHANT_KEY=token_hex(32), MONRI_AUTHENTICITY_TOKEN=token_hex(20))
        environment['TEST_EXPECTED_KEY'] = environment['MONRI_MERCHANT_KEY']
        environment['TEST_EXPECTED_TOKEN'] = environment['MONRI_AUTHENTICITY_TOKEN']
        script = r'''
import os
from pathlib import Path
from unittest.mock import patch
original_exists, original_read = Path.exists, Path.read_text
def exists(path):
    return True if path.name == '.env' else original_exists(path)
def read(path, *args, **kwargs):
    if path.name == '.env':
        return 'MONRI_MERCHANT_KEY=\nMONRI_AUTHENTICITY_TOKEN=\n'
    return original_read(path, *args, **kwargs)
with patch.object(Path, 'exists', exists), patch.object(Path, 'read_text', read):
    from EcommerceProject import settings
assert settings.MONRI_MERCHANT_KEY == os.environ['TEST_EXPECTED_KEY']
assert settings.MONRI_AUTHENTICITY_TOKEN == os.environ['TEST_EXPECTED_TOKEN']
assert settings.MONRI_ENABLED is True
assert settings.MONRI_ENVIRONMENT == 'test'
assert os.environ['MONRI_MERCHANT_KEY'] == os.environ['TEST_EXPECTED_KEY']
assert os.environ['MONRI_AUTHENTICITY_TOKEN'] == os.environ['TEST_EXPECTED_TOKEN']
'''
        # The child never prints keys; failed assertions also have no values.
        result = subprocess.run([sys.executable, '-c', script], env=environment, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, 'Django Monri environment precedence check failed.')

    def test_credentials_are_read_only_from_process_environment(self):
        from EcommerceProject.monri_config import read_monri_config
        from secrets import token_hex
        config = read_monri_config({}, {'MONRI_MERCHANT_KEY': token_hex(32),
                                        'MONRI_AUTHENTICITY_TOKEN': token_hex(20)})
        self.assertFalse(bool(config['MONRI_MERCHANT_KEY']))
        self.assertFalse(bool(config['MONRI_AUTHENTICITY_TOKEN']))


@override_settings(ALLOWED_HOSTS=['testserver'], MONRI_ENABLED=True, MONRI_ENVIRONMENT='production',
    MONRI_MERCHANT_KEY='private-test-key', MONRI_AUTHENTICITY_TOKEN='public-test-token',
    MONRI_PUBLIC_BASE_URL='https://carpologijabh.ba',
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class MonriProductionFinalizationTests(TransactionTestCase):
    def setUp(self):
        from .models import Product, OrderItem
        from .monri import create_checkout_payment
        product = Product.objects.create(naziv='Production callback fixture', cijena=10,
            stanje=5, aktivan=True, na_stanju=True)
        order = Order(ime_prezime='Test Kupac', email='test@example.com', telefon='061123456',
                      adresa='Ulica 1', grad='Sarajevo', ukupno=Decimal('10.00'))
        item = OrderItem(narudzba=order, artikal=product, naziv=product.naziv,
                        product_naziv=product.naziv, sifra='TEST', cijena=10, kolicina=1)
        session = self.client.session
        session['cart'] = {'fixture': {'quantity': 1}}
        session.save()
        self.payment = create_checkout_payment(order, [item], context={
            'session_key': session.session_key, 'cart': dict(session['cart'])})

    def callback(self, alternate_header=False):
        payload = {'id': 123, 'order_number': self.payment.reference, 'amount': 1000,
                   'currency': 'BAM', 'status': 'approved', 'response_code': '0000',
                   'transaction_type': 'purchase'}
        body = json.dumps(payload).encode('utf-8')
        signature = 'WP3-callback ' + hashlib.sha512(b'private-test-key' + body).hexdigest()
        header = 'HTTP_HTTP_AUTHORIZATION' if alternate_header else 'HTTP_AUTHORIZATION'
        return self.client.post(reverse('monri_callback'), body, content_type='application/json', **{header: signature})

    @override_settings(SITE_PREP_ENABLED=True, SITE_PREP_PASSWORD='fixture-site-lock')
    def test_production_callback_finalizes_existing_fulfillment_once(self):
        from contextlib import ExitStack
        from django.contrib.auth import get_user_model
        from .models import StaffSiteEvent, OrderStockHold
        from .views_magacin import collect_pick_jobs
        self.assertFalse(Order.objects.exists())
        with ExitStack() as stack:
            email = stack.enter_context(patch('EcommerceApp.emails.queue_order_emails'))
            loyalty = stack.enter_context(patch('EcommerceApp.views.azuriraj_loyalty_nakon_narudzbe', return_value=None))
            sync = stack.enter_context(patch('EcommerceApp.views.sync_narudzba', return_value=None))
            analytics = stack.enter_context(patch('EcommerceApp.meta_conversions.track_purchase'))
            courier = stack.enter_context(patch('EcommerceApp.xexpress_service.requests.post'))
            with self.assertLogs('EcommerceApp.monri', level='INFO') as captured:
                self.assertEqual(self.callback(alternate_header=True).status_code, 200)
            self.payment.refresh_from_db()
            order = self.payment.order
            self.assertEqual(self.payment.status, 'paid')
            self.assertIsNotNone(self.payment.paid_at)
            self.assertIsNotNone(self.payment.finalized_at)
            self.assertEqual(self.client.session['cart'], {})
            self.assertTrue(order.placeno_karticom())
            self.assertEqual(order.status, Order.Status.NOVA)
            self.assertEqual(Order.objects.count(), 1)
            holds = OrderStockHold.objects.filter(narudzba=order).count()
            self.assertGreater(holds, 0)
            self.assertIn(order.pk, [job.pk for job in collect_pick_jobs()])
            staff = get_user_model().objects.create_superuser(username='callback-staff', email='staff@example.com', password='fixture-only')
            self.client.force_login(staff)
            for route in ('staff_online_orders', 'staff_magacin_narudzbe'):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertIn(order.pk, [entry.pk for entry in response.context['orders']])
            self.assertEqual(self.callback().status_code, 200)
            self.assertEqual(self.callback().status_code, 200)
            self.assertEqual(Order.objects.count(), 1)
            self.assertEqual(OrderStockHold.objects.filter(narudzba=order).count(), holds)
            self.assertEqual(StaffSiteEvent.objects.filter(tip='purchase').count(), 1)
            email.assert_called_once()
            loyalty.assert_called_once()
            sync.assert_called_once()
            analytics.assert_called_once()
            self.assertEqual(analytics.call_args.kwargs['event_id'], f'purchase-{order.broj}')
            courier.assert_not_called()
            output = '\n'.join(captured.output)
            for event in ('MONRI_CALLBACK_RECEIVED', 'MONRI_CALLBACK_VERIFIED',
                          'MONRI_PAYMENT_MARKED_PAID', 'MONRI_ORDER_FINALIZED'):
                self.assertIn(event, output)
            for secret in ('private-test-key', 'public-test-token', 'WP3-callback', order.email):
                self.assertTrue(secret not in output)

    @override_settings(SITE_PREP_ENABLED=True, SITE_PREP_PASSWORD='fixture-site-lock')
    def test_site_lock_exempts_only_signed_callback_not_public_pages(self):
        response = self.client.post(reverse('monri_callback'), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse('home')).status_code, 302)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'pending')
        self.assertFalse(Order.objects.exists())

    def test_duplicate_callback_resumes_failure_without_repeating_completed_steps(self):
        from .models import StaffSiteEvent
        with patch('EcommerceApp.emails.queue_order_emails') as email, \
             patch('EcommerceApp.views.azuriraj_loyalty_nakon_narudzbe', side_effect=[RuntimeError('fixture failure'), None]) as loyalty, \
             patch('EcommerceApp.views.sync_narudzba', return_value=None) as sync, \
             patch('EcommerceApp.meta_conversions.track_purchase') as analytics:
            self.assertEqual(self.callback().status_code, 503)
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.status, 'paid')
            self.assertIsNone(self.payment.finalized_at)
            self.assertTrue(self.payment.finalization_steps['email'])
            self.assertEqual(self.callback().status_code, 200)
            self.payment.refresh_from_db()
            self.assertIsNotNone(self.payment.finalized_at)
            self.assertEqual(self.callback().status_code, 200)
            self.assertEqual(Order.objects.count(), 1)
            self.assertEqual(StaffSiteEvent.objects.filter(tip='purchase').count(), 1)
            email.assert_called_once()
            self.assertEqual(loyalty.call_count, 2)  # First attempt failed before completion.
            sync.assert_called_once()
            analytics.assert_called_once()


class MonriPostgresQueryTests(SimpleTestCase):
    def test_nullable_order_join_locks_only_payment_on_postgres(self):
        from django.db.backends.postgresql.base import DatabaseWrapper
        from .views_monri import callback_payment_queryset
        connection = DatabaseWrapper({'ENGINE': 'django.db.backends.postgresql', 'NAME': 'fixture',
            'OPTIONS': {}, 'AUTOCOMMIT': True, 'TIME_ZONE': None}, alias='compiler-only')
        with patch.object(connection, 'get_autocommit', return_value=False):
            sql, _ = callback_payment_queryset('Cfixture').query.get_compiler(connection=connection).as_sql()
        self.assertIn('LEFT OUTER JOIN', sql)
        self.assertIn('FOR UPDATE OF "EcommerceApp_cardpayment"', sql)
