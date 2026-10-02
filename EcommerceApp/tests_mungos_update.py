import logging
from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

from .models import Category, Product
from .mungos_product import build_product_preview
from .mungos_update import build_mungos_update_payload


EXPECTED_PUT = {
    'id': '7889', 'name': 'MT14007 MATE M8 CARP REEL 8000 FD',
    'categoryUuid': None,
    'categoryCode': 'SportRecreation_Equipment_FishingEquipment_Reels',
    'brandCode': None, 'hasQuantities': True, 'quantityRemaining': 43,
    'shortDescription': 'Opis', 'details': 'Opis', 'productType': 'Product',
    'price': 138.0, 'currencyIsoCode': 'BAM', 'isNegotiable': False,
    'isFree': False, 'sku': '7889', 'ean': '1234567890123',
    'warrantyMonthsCount': None, 'warrantyDescription': None,
    'returnDaysCount': None, 'returnDescription': None,
    'sellerPaysForReturnShipping': True, 'exchangeAcceptable': False,
    'exchangeComment': None, 'shippmentDeliveryMethod': 'DeliveryByMe',
    'condition': 'New', 'countryCode': 'BA', 'cityCode': 'Bijeljina',
    'streetName': None, 'postalCode': None, 'longitude': None, 'latitude': None,
    'productAttributes': {},
    'images': [{'imageUrl': 'https://example.com/reel.jpg', 'isMainImage': True}],
}


@override_settings(
    MUNGOS_ENABLED=True,
    MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector/',
    MUNGOS_API_KEY='test-key', MUNGOS_ECOMMERCE_ACCESS_CODE='test-access',
)
class MungosUpdateTests(SimpleTestCase):
    def setUp(self):
        self.http_patch = patch('EcommerceApp.mungos_client.requests.Session')
        self.factory = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)
        self.session = self.factory.return_value.__enter__.return_value
        self.response = self.session.put.return_value.__enter__.return_value
        self.response.status_code = 200
        self.response.iter_content.return_value = [b'{"message":"created"}']
        self.product_patch = patch('EcommerceApp.management.commands.mungos_product_update.Product')
        self.product = self.product_patch.start()
        self.addCleanup(self.product_patch.stop)
        self.builder_patch = patch('EcommerceApp.management.commands.mungos_product_update.build_product_preview')
        self.builder = self.builder_patch.start()
        self.addCleanup(self.builder_patch.stop)
        self.preview = {'status': 'READY_FOR_REVIEW', 'reviewReasons': [],
                        'payload': {**EXPECTED_PUT, 'HasVariants': False, 'Variants': [], 'extraCreateField': True}}
        self.builder.return_value = self.preview
        self.output = StringIO()
        self.logs = StringIO()
        handler = logging.StreamHandler(self.logs)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)

    def run_command(self, confirm=True):
        call_command('mungos_product_update', 4455, 'a9241b59-9e45-4840-9730-c93cb8ad9517', confirm=confirm,
                     stdout=self.output, stderr=self.output)

    def test_without_confirm_no_http_or_database_or_builder(self):
        self.run_command(confirm=False)
        self.assertIn('NOT_SENT', self.output.getvalue())
        self.factory.assert_not_called()
        self.product.objects.select_related.assert_not_called()
        self.builder.assert_not_called()

    def test_confirm_exactly_one_put_with_exact_confirmed_put_payload(self):
        self.run_command()
        self.builder.assert_called_once()
        self.session.put.assert_called_once_with(
            'https://staging.mungos.ba/api/v1/connector/standard/product/a9241b59-9e45-4840-9730-c93cb8ad9517',
            json=EXPECTED_PUT,
            headers={'X-Api-Key': 'test-key', 'ecommerceaccesscode': 'test-access'},
            timeout=(5, 10), allow_redirects=False, stream=True,
        )
        self.assertEqual(self.session.put.call_args.kwargs['json'], EXPECTED_PUT)
        self.session.post.assert_not_called()
        self.session.get.assert_not_called()
        self.session.request.assert_not_called()
        self.assertIn('SUCCESS', self.output.getvalue())

    def test_invalid_uuid_never_sends(self):
        from .mungos_client import MungosClient, MungosError
        for value in ('', 'bad', '../product', 'a9241b599e4548409730c93cb8ad9517',
                      'a9241b59-9e45-4840-9730-c93cb8ad9517?x=1', None):
            with self.subTest(value=value):
                with self.assertRaisesMessage(CommandError, 'NOT_SENT'):
                    call_command('mungos_product_update', 4455, value, confirm=True, stdout=self.output)
                with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
                    MungosClient().update_product(value, {})
        self.factory.assert_not_called()
        self.builder.assert_not_called()

    def test_current_builder_payload_is_rebuilt_each_invocation(self):
        self.run_command()
        current = {'status': 'READY_FOR_REVIEW', 'reviewReasons': [],
                   'payload': {**EXPECTED_PUT, 'sku': 'current', 'name': 'Current', 'price': 99,
                               'quantityRemaining': 7, 'details': 'Current description',
                               'images': [], 'categoryCode': 'Reels', 'Variants': []}}
        self.builder.return_value = current
        self.session.put.reset_mock()
        self.run_command()
        self.session.put.assert_called_once()
        self.assertEqual(self.session.put.call_args.kwargs['json'],
                         {key: value for key, value in current['payload'].items() if key != 'Variants'})
        self.assertEqual(self.builder.call_count, 2)

    def test_variants_block_put_even_if_create_preview_is_ready(self):
        for marker in ({'HasVariants': True}, {'Variants': [{'sku': 'variant'}]}):
            with self.subTest(marker=marker):
                self.preview['payload'].update(marker)
                with self.assertRaisesMessage(CommandError, 'NOT_SENT'):
                    self.run_command()
        self.factory.assert_not_called()

    def test_rate_limit_has_specific_message(self):
        self.response.status_code = 429
        with self.assertRaisesMessage(CommandError, 'Rate limit'):
            self.run_command()
        self.session.put.assert_called_once()

    def test_review_status_or_reasons_prevent_send(self):
        for status, reasons in [('NEEDS_REVIEW', []), ('READY_FOR_REVIEW', ['issue'])]:
            with self.subTest(status=status):
                self.preview.update(status=status, reviewReasons=reasons)
                with self.assertRaisesMessage(CommandError, 'NOT_SENT'):
                    self.run_command()
        self.factory.assert_not_called()

    def test_http_errors_and_redirects_never_retry_and_redact_response(self):
        for status in (302, 400, 401, 403, 404, 409, 429, 500):
            with self.subTest(status=status):
                self.session.put.reset_mock()
                self.response.status_code = status
                self.response.iter_content.return_value = [b'{"test-key":"test-access", "echo":"test-\\u006bey"}']
                with self.assertRaisesMessage(CommandError, f'HTTP status: {status}') as caught:
                    self.run_command()
                self.session.put.assert_called_once()
                for secret in ('test-key', 'test-access'):
                    self.assertNotIn(secret, self.output.getvalue() + self.logs.getvalue() + str(caught.exception))
                self.assertIn('[REDACTED]', self.output.getvalue())

    def test_timeout_network_errors_are_unknown_and_never_retry(self):
        for error in (requests.Timeout, requests.ConnectionError, requests.RequestException):
            with self.subTest(error=error):
                self.session.put.reset_mock()
                self.session.put.side_effect = error('test-key test-access')
                with self.assertRaisesMessage(CommandError, 'UNKNOWN_REMOTE_STATE') as caught:
                    self.run_command()
                self.session.put.assert_called_once()
                self.assertNotIn('test-key', str(caught.exception))
                self.assertNotIn('test-access', str(caught.exception))

    def test_response_read_error_preserves_status_and_unknown_state(self):
        self.response.iter_content.side_effect = requests.ConnectionError('test-key')
        with self.assertRaisesMessage(CommandError, 'UNKNOWN_REMOTE_STATE | HTTP status: 200'):
            self.run_command()
        self.session.put.assert_called_once()

    def test_plain_text_response_and_size_limit(self):
        self.response.iter_content.return_value = [b'test-key test-access']
        self.run_command()
        self.assertIn('[REDACTED]', self.output.getvalue())
        self.assertNotIn('test-key', self.output.getvalue())
        self.response.iter_content.return_value = [b'x' * 4096] * 17
        self.run_command()
        self.assertIn('skraćen', self.output.getvalue())

    def test_disabled_missing_auth_or_non_staging_never_connect(self):
        for config in ({'MUNGOS_ENABLED': False}, {'MUNGOS_API_KEY': ''},
                       {'MUNGOS_ECOMMERCE_ACCESS_CODE': ''},
                       {'MUNGOS_BASE_URL': 'https://mungos.ba/api/v1/connector'}):
            with self.subTest(config=config), override_settings(**config):
                with self.assertRaises(CommandError):
                    self.run_command()
        self.factory.assert_not_called()


@override_settings(MUNGOS_ENABLED=True, MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                   MUNGOS_API_KEY='test-key', MUNGOS_ECOMMERCE_ACCESS_CODE='test-access',
                   SITE_URL='https://example.com')
class MungosUpdateReadOnlyTests(TestCase):
    def test_real_builder_payload_and_select_only(self):
        category = Category.objects.create(naziv='Mašinice')
        product = Product.objects.create(naziv=EXPECTED_PUT['name'], sifra='7889', cijena=138,
                                         opis='Opis', barkod='1234567890123', slika='reel.jpg',
                                         stanje=43, aktivan=True, na_stanju=True, kategorija=category)
        expected = {**EXPECTED_PUT, 'images': [
            {'imageUrl': product.slika.url if product.slika.url.startswith('https://')
             else 'https://example.com' + product.slika.url, 'isMainImage': True}]}
        self.assertEqual(build_mungos_update_payload(build_product_preview(product))['payload'], expected)
        with patch('EcommerceApp.mungos_client.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.put.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_content.return_value = [b'{}']
            with CaptureQueriesContext(connection) as queries:
                call_command('mungos_product_update', product.pk, 'a9241b59-9e45-4840-9730-c93cb8ad9517', confirm=True, stdout=StringIO())
            self.assertTrue(queries)
            self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
            session.put.assert_called_once()
            self.assertEqual(session.put.call_args.kwargs['json'], expected)
        product.refresh_from_db()
        self.assertEqual((product.stanje, product.cijena, product.sifra), (43, 138, '7889'))


class MungosUpdateAdapterTests(SimpleTestCase):
    def test_exact_schema_without_mutating_create_payload(self):
        from copy import deepcopy
        preview = {'status': 'READY_FOR_REVIEW', 'reviewReasons': [],
                   'payload': {**EXPECTED_PUT, 'HasVariants': False, 'Variants': []}}
        original = deepcopy(preview)
        adapted = build_mungos_update_payload(preview)
        self.assertEqual(adapted['payload'], EXPECTED_PUT)
        self.assertEqual(preview, original)

    def test_missing_ean_and_brand_have_only_confirmed_empty_defaults(self):
        payload = {key: value for key, value in EXPECTED_PUT.items()
                   if key not in ('ean', 'brandCode')}
        adapted = build_mungos_update_payload(
            {'status': 'READY_FOR_REVIEW', 'reviewReasons': [], 'payload': payload})
        self.assertEqual(adapted['payload'], {**EXPECTED_PUT, 'ean': '', 'brandCode': None})

    def test_missing_required_fields_need_review(self):
        adapted = build_mungos_update_payload(
            {'status': 'READY_FOR_REVIEW', 'reviewReasons': [], 'payload': {}})
        self.assertEqual(adapted['status'], 'NEEDS_REVIEW')
        self.assertTrue(adapted['reviewReasons'])
        self.assertIsNone(adapted['payload'])
