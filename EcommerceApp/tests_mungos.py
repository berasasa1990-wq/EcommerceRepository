from io import StringIO
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from .mungos_client import MungosClient


@override_settings(
    MUNGOS_ENABLED=True,
    MUNGOS_BASE_URL='https://staging.mungos.example/api/v1/connector/',
    MUNGOS_API_KEY='test-secret-do-not-print',
    MUNGOS_ECOMMERCE_ACCESS_CODE='test-access-code-do-not-print',
)
class MungosLivenessTests(SimpleTestCase):
    # SimpleTestCase forbids database queries; all HTTP calls are mocked.
    def setUp(self):
        self.session_patch = patch('EcommerceApp.mungos_client.requests.Session')
        self.session_factory = self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        self.session = self.session_factory.return_value.__enter__.return_value
        self.response = self.session.get.return_value.__enter__.return_value
        self.response.status_code = 200

    def run_command(self):
        output = StringIO()
        call_command('mungos_liveness', stdout=output, stderr=output)
        return output.getvalue()

    def test_success_sends_single_get_with_safe_options(self):
        self.assertIn('SUCCESS | HTTP status: 200', self.run_command())
        self.session.get.assert_called_once_with(
            'https://staging.mungos.example/api/v1/connector/Liveness/check/hello',
            headers={'X-Api-Key': 'test-secret-do-not-print',
                     'ecommerceaccesscode': 'test-access-code-do-not-print'},
            timeout=(5, 10), allow_redirects=False, stream=True,
        )
        self.session.post.assert_not_called()
        self.session.request.assert_not_called()
        self.response.json.assert_not_called()
        self.session.get.return_value.__exit__.assert_called_once()

    def test_all_2xx_are_success(self):
        self.response.status_code = 204
        self.assertIn('SUCCESS | HTTP status: 204', self.run_command())

    def test_non_2xx_and_redirects_fail_without_body_or_secret(self):
        for status in (301, 302, 400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                self.response.status_code = status
                with self.assertRaisesMessage(CommandError, f'FAILED | HTTP status: {status}') as caught:
                    self.run_command()
                self.assertNotIn('test-access-code-do-not-print', str(caught.exception))
        self.response.json.assert_not_called()

    def test_transport_errors_are_sanitized(self):
        for error in (requests.Timeout, requests.ConnectionError, requests.exceptions.SSLError,
                      requests.RequestException):
            with self.subTest(error=error):
                self.session.get.side_effect = error('test-secret-do-not-print test-access-code-do-not-print')
                with self.assertRaises(CommandError) as caught:
                    self.run_command()
                self.assertIn('FAILED | HTTP status: N/A', str(caught.exception))
                self.assertNotIn('test-secret-do-not-print', str(caught.exception))
                self.assertNotIn('test-access-code-do-not-print', str(caught.exception))

    def test_disabled_never_opens_connection(self):
        with override_settings(MUNGOS_ENABLED=False):
            with self.assertRaisesMessage(CommandError, 'Mungos je isključen'):
                self.run_command()
        self.session_factory.assert_not_called()

    def test_missing_or_invalid_config_never_connects_or_exposes_values(self):
        configs = [
            {'MUNGOS_BASE_URL': ''},
            {'MUNGOS_API_KEY': ''},
            {'MUNGOS_ECOMMERCE_ACCESS_CODE': 'test-access-code-do-not-print\r\nInjected: yes'},
            {'MUNGOS_ECOMMERCE_ACCESS_CODE': ' test-access-code-do-not-print'},
            {'MUNGOS_ECOMMERCE_ACCESS_CODE': 'test-access-code-do-not-print\u0107'},
            {'MUNGOS_API_KEY': 'test-secret-do-not-print\r\nInjected: yes'},
            {'MUNGOS_BASE_URL': 'http://staging.mungos.example'},
            {'MUNGOS_BASE_URL': 'https://user:test-secret-do-not-print@staging.mungos.example'},
            {'MUNGOS_BASE_URL': 'https://staging.mungos.example/?key=test-secret-do-not-print'},
            {'MUNGOS_BASE_URL': 'https://staging.mungos.example/#test-secret-do-not-print'},
            {'MUNGOS_BASE_URL': 'https://staging.mungos.example:invalid'},
            {'MUNGOS_BASE_URL': 'https://[invalid'},
        ]
        for config in configs:
            with self.subTest(config=list(config)), override_settings(**config):
                with self.assertRaises(CommandError) as caught:
                    self.run_command()
                self.assertNotIn('test-secret-do-not-print', str(caught.exception))
                self.assertNotIn('test-access-code-do-not-print', str(caught.exception))
        self.session_factory.assert_not_called()

    def test_production_switch_only_changes_base_url(self):
        with override_settings(MUNGOS_BASE_URL='https://production.mungos.example'):
            self.run_command()
        self.assertEqual(self.session.get.call_args.args[0],
                         'https://production.mungos.example' + MungosClient.LIVENESS_PATH)

    def test_success_output_does_not_contain_key(self):
        output = self.run_command()
        self.assertNotIn('test-secret-do-not-print', output)
        self.assertNotIn('test-access-code-do-not-print', output)

    def test_access_code_can_be_omitted_for_environments_without_extra_protection(self):
        with override_settings(MUNGOS_ECOMMERCE_ACCESS_CODE=''):
            self.run_command()
        self.assertEqual(self.session.get.call_args.kwargs['headers'],
                         {'X-Api-Key': 'test-secret-do-not-print'})
