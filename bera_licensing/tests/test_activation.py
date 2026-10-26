import base64
import json
from datetime import timedelta
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from bera_licensing.models import LicenseConnection, LicenseState
from bera_licensing.credentials import api_credential
from bera_licensing.security import canonical_payload, LicenseError
from bera_licensing.services import activate_license, module_enabled, sync_license


@override_settings(BERA_LICENSE_INSTALLATION_ID='', BERA_LICENSE_API_CREDENTIAL='', BERA_LICENSE_ENFORCED_MODULES=())
class ActivationTests(TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        self.encryption = Fernet.generate_key().decode()
        config = override_settings(BERA_LICENSE_PUBLIC_KEY=self.public, BERA_LICENSE_ENCRYPTION_KEY=self.encryption)
        config.enable()
        self.addCleanup(config.disable)
        self.user = get_user_model().objects.create_user('activation-admin', is_staff=True)
        self.client.force_login(self.user)

    def document(self, enabled=True, version=1):
        now = timezone.now()
        doc = dict(installation_id='activated-install', protocol_version=1, license_version=version,
                   issued_at=(now-timedelta(minutes=1)).isoformat(), expires_at=(now+timedelta(hours=1)).isoformat(),
                   grace_until=(now+timedelta(hours=2)).isoformat(), modules={'loyalty': enabled})
        doc['signature'] = base64.b64encode(self.key.sign(canonical_payload(doc))).decode()
        return doc

    @patch('bera_licensing.client.activate_remote', return_value={'api_credential': 'super-secret-credential', 'public_key': 'untrusted'})
    @patch('bera_licensing.services.fetch_license')
    def test_activation_encryption_sync_and_backend(self, fetch, activate):
        fetch.return_value = self.document(False)
        response = self.client.post(reverse('bera_license_activate'), {'installation_id': 'activated-install', 'activation_code': 'one-time-secret'}, follow=True)
        activate.assert_called_once_with('activated-install', 'one-time-secret')
        stored = LicenseConnection.objects.get()
        self.assertNotIn('super-secret-credential', stored.encrypted_credential)
        self.assertEqual(api_credential(), 'super-secret-credential')
        self.assertIsNotNone(LicenseState.objects.get().last_success)
        self.assertFalse(module_enabled('loyalty'))
        self.assertEqual(self.client.get(reverse('loyalty')).status_code, 403)
        self.assertEqual(self.client.post(reverse('module_loyalty')).status_code, 410)
        self.assertNotContains(response, 'super-secret-credential')
        self.assertNotContains(response, 'one-time-secret')
        self.assertContains(response, 'moduleActivationDialog')
        self.assertNotContains(response, 'Installation ID')
        self.assertEqual(self.settings_value(), self.public)
        fetch.return_value = self.document(True, 2)
        self.client.post(reverse('bera_license_sync'))
        self.assertTrue(module_enabled('loyalty'))

    def settings_value(self):
        from django.conf import settings
        return settings.BERA_LICENSE_PUBLIC_KEY

    @patch('bera_licensing.client.activate_remote', side_effect=LicenseError('invalid_activation_code'))
    def test_invalid_code(self, activate):
        response = self.client.post(reverse('bera_license_activate'), {'installation_id': 'activated-install', 'activation_code': 'invalid-secret'}, follow=True)
        self.assertFalse(LicenseConnection.objects.exists())
        self.assertNotContains(response, 'invalid-secret')
        self.assertContains(response, 'nije uspjela')

    @patch('bera_licensing.client.activate_remote')
    def test_local_configuration_checked_before_consuming_code(self, activate):
        for config in ({'BERA_LICENSE_ENCRYPTION_KEY': ''}, {'BERA_LICENSE_PUBLIC_KEY': ''}):
            with override_settings(**config), self.assertRaises(LicenseError):
                activate_license('activated-install', 'code')
        activate.assert_not_called()

    @patch('bera_licensing.client.activate_remote', return_value={'api_credential': 'secret'})
    @patch('bera_licensing.services.fetch_license', side_effect=LicenseError('sync_unavailable'))
    def test_sync_failure_keeps_encrypted_credential_for_retry(self, fetch, activate):
        with self.assertRaises(LicenseError):
            activate_license('activated-install', 'code')
        self.assertEqual(api_credential(), 'secret')
        with override_settings(BERA_LICENSE_ENCRYPTION_KEY=Fernet.generate_key().decode()), self.assertRaises(LicenseError):
            api_credential()
        with self.assertRaises(LicenseError):
            activate_license('activated-install', 'another-code')
        self.assertEqual(activate.call_count, 1)

    @patch('bera_licensing.client.activate_remote', return_value={'api_credential': 'secret'})
    @patch('bera_licensing.services.fetch_license')
    def test_response_key_cannot_replace_trusted_key(self, fetch, activate):
        self.key = Ed25519PrivateKey.generate()
        fetch.return_value = self.document()
        activate.return_value['public_key'] = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        with self.assertRaises(LicenseError):
            activate_license('activated-install', 'code')
        self.assertFalse(module_enabled('loyalty'))
        self.assertIsNone(LicenseState.objects.get().last_success)

    def test_auth_post_and_csrf(self):
        self.assertEqual(self.client.get(reverse('bera_license_activate')).status_code, 405)
        self.assertEqual(self.client.get(reverse('bera_license_sync')).status_code, 405)
        from django.test import Client
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(reverse('bera_license_activate'), {}).status_code, 403)
        self.user.is_staff = False
        self.user.save()
        self.assertEqual(self.client.post(reverse('bera_license_sync')).status_code, 302)

    @patch('bera_licensing.client.build_opener')
    def test_wire_protocol_and_secret_error(self, opener):
        from bera_licensing.client import activate_remote
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'api_credential': 'wire-secret'}).encode()
        opener.return_value.open.return_value = response
        self.assertEqual(activate_remote('id', 'one-time')['api_credential'], 'wire-secret')
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://www.bera-it.com/api/v1/licensing/activate/')
        self.assertEqual(json.loads(request.data)['activation_code'], 'one-time')
        self.assertFalse(request.has_header('Authorization'))
        opener.return_value.open.side_effect = RuntimeError('wire-secret one-time')
        with self.assertRaises(LicenseError) as caught:
            activate_remote('id', 'one-time')
        self.assertEqual(str(caught.exception), 'server_unavailable')

    @patch('bera_licensing.client.build_opener')
    def test_scheduler_uses_database_credential(self, opener):
        from io import StringIO
        from django.core.management import call_command
        LicenseConnection.objects.create(pk=1, installation_id='activated-install',
            encrypted_credential=Fernet(self.encryption.encode()).encrypt(b'database-secret').decode())
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(self.document()).encode()
        opener.return_value.open.return_value = response
        output = StringIO()
        call_command('sync_bera_license', stdout=output)
        self.assertIsNotNone(LicenseState.objects.get().last_success)
        request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer database-secret')
        self.assertEqual(json.loads(request.data)['installation_id'], 'activated-install')
        self.assertNotIn('database-secret', output.getvalue())
        call_command('sync_bera_license', stdout=output)
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch('bera_licensing.client.build_opener')
    def test_invalid_code_http_response_is_not_exposed(self, opener):
        from io import BytesIO
        from urllib.error import HTTPError
        from bera_licensing.client import activate_remote
        opener.return_value.open.side_effect = HTTPError('https://example.com', 400,
            'secret-code credential', {}, BytesIO(b'credential secret-code'))
        with self.assertRaises(LicenseError) as caught:
            activate_remote('id', 'secret-code')
        self.assertEqual(str(caught.exception), 'invalid_activation_code')
        self.assertFalse(LicenseConnection.objects.exists())
