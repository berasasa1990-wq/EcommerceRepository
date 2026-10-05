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
from .tests_mungos_payload import create_example


@override_settings(
    MUNGOS_ENABLED=True,
    MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector/',
    MUNGOS_API_KEY='test-key', MUNGOS_ECOMMERCE_ACCESS_CODE='test-access',
)
class MungosSendTests(SimpleTestCase):
    def setUp(self):
        self.http_patch = patch('EcommerceApp.mungos_client.requests.Session')
        self.factory = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)
        self.session = self.factory.return_value.__enter__.return_value
        self.response = self.session.post.return_value.__enter__.return_value
        self.response.status_code = 201
        self.response.iter_content.return_value = [b'{"message":"created"}']
        self.product_patch = patch('EcommerceApp.management.commands.mungos_product_send.Product')
        self.product = self.product_patch.start()
        self.addCleanup(self.product_patch.stop)
        self.builder_patch = patch('EcommerceApp.management.commands.mungos_product_send.build_product_preview')
        self.builder = self.builder_patch.start()
        self.addCleanup(self.builder_patch.stop)
        self.preview = {'status': 'READY_FOR_REVIEW', 'reviewReasons': [],
                        'payload': create_example()}
        self.builder.return_value = self.preview
        self.output = StringIO()

    def run_command(self, confirm=True):
        call_command('mungos_product_send', 4455, confirm=confirm,
                     stdout=self.output, stderr=self.output)

    def test_without_confirm_no_http_or_database_or_builder(self):
        self.run_command(confirm=False)
        self.assertIn('Slanje nije izvršeno', self.output.getvalue())
        self.factory.assert_not_called()
        self.product.objects.select_related.assert_not_called()
        self.builder.assert_not_called()

    def test_confirm_exactly_one_post_with_original_builder_payload(self):
        self.run_command()
        self.builder.assert_called_once()
        self.session.post.assert_called_once_with(
            'https://staging.mungos.ba/api/v1/connector/standard/product',
            json=self.preview['payload'],
            headers={'X-Api-Key': 'test-key', 'ecommerceaccesscode': 'test-access'},
            timeout=(5, 10), allow_redirects=False, stream=True,
        )
        self.assertEqual(self.session.post.call_args.kwargs['json'], self.preview['payload'])
        self.session.put.assert_not_called()
        self.session.get.assert_not_called()
        self.session.request.assert_not_called()
        self.assertIn('SUCCESS', self.output.getvalue())

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
                self.session.post.reset_mock()
                self.response.status_code = status
                self.response.iter_content.return_value = [b'{"test-key":"test-access", "echo":"test-\\u006bey"}']
                with self.assertRaisesMessage(CommandError, f'HTTP status: {status}') as caught:
                    self.run_command()
                self.session.post.assert_called_once()
                self.session.put.assert_not_called()
                for secret in ('test-key', 'test-access'):
                    self.assertNotIn(secret, self.output.getvalue() + str(caught.exception))
                self.assertIn('[REDACTED]', self.output.getvalue())

    def test_timeout_network_errors_are_unknown_and_never_retry(self):
        for error in (requests.Timeout, requests.ConnectionError, requests.RequestException):
            with self.subTest(error=error):
                self.session.post.reset_mock()
                self.session.post.side_effect = error('test-key test-access')
                with self.assertRaisesMessage(CommandError, 'UNKNOWN_REMOTE_STATE') as caught:
                    self.run_command()
                self.session.post.assert_called_once()
                self.assertNotIn('test-key', str(caught.exception))
                self.assertNotIn('test-access', str(caught.exception))

    def test_response_read_error_preserves_status_and_unknown_state(self):
        self.response.iter_content.side_effect = requests.ConnectionError('test-key')
        with self.assertRaisesMessage(CommandError, 'UNKNOWN_REMOTE_STATE | HTTP status: 201'):
            self.run_command()
        self.session.post.assert_called_once()

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
                   MUNGOS_API_KEY='test-key', MUNGOS_ECOMMERCE_ACCESS_CODE='test-access')
class MungosSendReadOnlyTests(TestCase):
    def test_real_builder_payload_and_select_only(self):
        category = Category.objects.create(naziv='Mašinice')
        product = Product.objects.create(naziv='Test', sifra='7889', cijena=138,
                                         stanje=43, aktivan=True, na_stanju=True, kategorija=category)
        expected = build_product_preview(product)['payload']
        with patch('EcommerceApp.mungos_client.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.post.return_value.__enter__.return_value
            response.status_code = 201
            response.iter_content.return_value = [b'{}']
            with CaptureQueriesContext(connection) as queries:
                call_command('mungos_product_send', product.pk, confirm=True, stdout=StringIO())
            self.assertTrue(queries)
            self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
            session.post.assert_called_once()
            self.assertEqual(session.post.call_args.kwargs['json'], expected)
        product.refresh_from_db()
        self.assertEqual((product.stanje, product.cijena, product.sifra), (43, 138, '7889'))
