from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core.exceptions import PermissionDenied
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from bera_licensing.services import module_enabled, require_module
from .models import ControlState, ModuleSwitch, AuditEvent, ModuleActivation
from .services import issue_bootstrap, initialize, recover, display_activation_code, redeem_module_code

PASSWORD = 'River!Stone-Control-7294'


class ControlTests(TestCase):
    def initialize(self):
        token = issue_bootstrap()
        initialize(token, PASSWORD)
        return token

    def login(self, client=None):
        return (client or self.client).post('/bera-control/', {'password': PASSWORD})

    def test_bootstrap_requires_valid_once_only_token(self):
        self.assertEqual(self.client.post('/bera-control/setup/', {'token': 'bad', 'password': PASSWORD, 'confirm': PASSWORD}).status_code, 403)
        token = issue_bootstrap()
        ControlState.objects.filter(pk=1).update(bootstrap_expires=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(PermissionDenied):
            initialize(token, PASSWORD)
        token = issue_bootstrap()
        self.assertEqual(self.client.post('/bera-control/setup/', {'token': token, 'password': PASSWORD, 'confirm': PASSWORD}).status_code, 302)
        state = ControlState.objects.get(pk=1)
        self.assertTrue(check_password(PASSWORD, state.password_hash))
        self.assertNotEqual(state.password_hash, PASSWORD)
        self.assertFalse(state.bootstrap_digest)
        self.assertEqual(self.client.get('/bera-control/setup/').status_code, 404)
        with self.assertRaises(PermissionDenied):
            initialize(token, PASSWORD)
        with self.assertRaises(PermissionDenied):
            issue_bootstrap()

    def test_bootstrap_rotation_and_weak_password(self):
        from django.core.exceptions import ValidationError
        old = issue_bootstrap()
        new = issue_bootstrap()
        with self.assertRaises(PermissionDenied):
            initialize(old, PASSWORD)
        with self.assertRaises(ValidationError):
            initialize(new, '123')
        initialize(new, PASSWORD)

    def test_password_login_and_global_lockout(self):
        self.initialize()
        for _ in range(5):
            self.client.post('/bera-control/', {'password': 'wrong'})
        self.login(Client())
        self.assertEqual(AuditEvent.objects.filter(action='login_success').count(), 0)
        self.assertTrue(ControlState.objects.get(pk=1).locked_until > timezone.now())
        ControlState.objects.filter(pk=1).update(locked_until=timezone.now()-timedelta(seconds=1))
        self.login()
        self.assertEqual(self.client.get('/bera-control/dashboard/').status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action='login_success').count(), 1)

    def test_admin_has_no_control_access(self):
        self.initialize()
        user = get_user_model().objects.create_superuser('owner', 'owner@example.com', PASSWORD)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/bera-control/dashboard/').status_code, 302)
        self.assertEqual(self.client.post('/bera-control/modules/loyalty/', {'enabled': 'off'}).status_code, 302)
        self.assertFalse(ModuleSwitch.objects.exists())

    def test_idle_timeout_logout_and_recovery(self):
        self.initialize()
        self.login()
        session = self.client.session
        auth = session['bera_control']
        auth['seen'] -= 1801
        session['bera_control'] = auth
        session.save()
        self.assertEqual(self.client.get('/bera-control/dashboard/').status_code, 302)
        self.login()
        recover(PASSWORD + '!')
        self.assertEqual(self.client.get('/bera-control/dashboard/').status_code, 302)
        self.assertEqual(self.client.get('/bera-control/setup/').status_code, 404)
        self.client.post('/bera-control/', {'password': PASSWORD + '!'})
        self.assertEqual(self.client.get('/bera-control/logout/').status_code, 405)
        self.client.post('/bera-control/logout/')
        self.assertEqual(self.client.get('/bera-control/dashboard/').status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(action='password_recovered').exists())

    @override_settings(BERA_LICENSE_ENFORCED_MODULES=(), BERA_LICENSE_INSTALLATION_ID='', BERA_LICENSE_API_CREDENTIAL='')
    def test_toggle_blocks_backend_preserves_data_and_survives_new_connection(self):
        from shop.models import LoyaltyTier
        self.initialize()
        self.login()
        tier = LoyaltyTier.objects.create(minimum=0, percent=10)
        self.assertTrue(module_enabled('loyalty'))
        self.client.post('/bera-control/modules/loyalty/', {'enabled': 'off'})
        self.assertFalse(module_enabled('loyalty'))
        with self.assertRaises(PermissionDenied):
            require_module('loyalty')
        self.assertEqual(self.client.get('/loyalty/').status_code, 403)
        self.assertTrue(LoyaltyTier.objects.filter(pk=tier.pk).exists())
        # New HTTP session/process reads database state, no in-memory switch cache.
        self.assertEqual(Client().get('/loyalty/').status_code, 403)
        with connection.cursor() as cursor:
            cursor.execute('SELECT enabled FROM bera_control_moduleswitch WHERE code = %s', ['loyalty'])
            self.assertFalse(cursor.fetchone()[0])
        self.client.post('/bera-control/modules/loyalty/', {'enabled': 'on'})
        activation = ModuleActivation.objects.get(module='loyalty')
        self.assertFalse(module_enabled('loyalty'))
        self.assertTrue(redeem_module_code('loyalty', display_activation_code(activation)))
        self.assertTrue(module_enabled('loyalty'))
        self.assertTrue(LoyaltyTier.objects.filter(pk=tier.pk).exists())
        self.assertEqual(AuditEvent.objects.filter(action='module_changed').count(), 2)

    @override_settings(BERA_LICENSE_ENFORCED_MODULES=('loyalty',), BERA_LICENSE_INSTALLATION_ID='missing')
    def test_control_independent_of_license_but_cannot_unlock_it(self):
        self.initialize()
        self.login()
        self.assertEqual(self.client.get('/bera-control/dashboard/').status_code, 200)
        self.client.post('/bera-control/modules/loyalty/', {'enabled': 'on'})
        self.assertFalse(module_enabled('loyalty'))
        self.assertEqual(self.client.post('/bera-control/modules/newsletter/', {'enabled': 'on'}).status_code, 404)
        self.assertFalse(ModuleSwitch.objects.filter(code='newsletter').exists())

    def test_csrf_and_post_only(self):
        self.initialize()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/bera-control/', {'password': PASSWORD}).status_code, 403)
        self.assertEqual(client.post('/bera-control/setup/', {}).status_code, 403)
        client.get('/bera-control/')
        token = client.cookies['csrftoken'].value
        client.post('/bera-control/', {'password': PASSWORD, 'csrfmiddlewaretoken': token})
        self.assertEqual(client.post('/bera-control/modules/loyalty/', {'enabled': 'off'}).status_code, 403)
        self.assertEqual(client.post('/bera-control/logout/').status_code, 403)
        self.assertEqual(client.get('/bera-control/modules/loyalty/').status_code, 405)
        response = client.get('/bera-control/dashboard/')
        self.assertIn('no-store', response['Cache-Control'])

    def test_form_origin_policy_and_csrf_origin_enforcement(self):
        self.initialize()
        client = Client(enforce_csrf_checks=True)
        response = client.get('/bera-control/', secure=True)
        self.assertContains(response, 'name="referrer" content="same-origin"')
        self.assertNotContains(response, 'name="referrer" content="no-referrer"')
        token = client.cookies['csrftoken'].value
        for origin in ('null', 'https://attacker.example'):
            response = client.post('/bera-control/', {
                'password': PASSWORD, 'csrfmiddlewaretoken': token,
            }, secure=True, HTTP_ORIGIN=origin)
            self.assertEqual(response.status_code, 403)
        response = client.post('/bera-control/', {
            'password': PASSWORD, 'csrfmiddlewaretoken': token,
        }, secure=True, HTTP_ORIGIN='https://testserver', HTTP_REFERER='https://testserver/bera-control/')
        self.assertEqual(response.status_code, 302)
        token = client.cookies['csrftoken'].value
        response = client.post('/bera-control/modules/loyalty/', {
            'enabled': 'off', 'csrfmiddlewaretoken': token,
        }, secure=True, HTTP_ORIGIN='https://testserver', HTTP_REFERER='https://testserver/bera-control/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ModuleSwitch.objects.get(code='loyalty').enabled)

    def test_management_commands_and_recovery_confirmation(self):
        out = StringIO()
        call_command('bera_control_bootstrap', stdout=out)
        self.assertTrue(ControlState.objects.get(pk=1).bootstrap_digest)
        self.initialize()
        with self.assertRaises(CommandError):
            call_command('bera_control_bootstrap', stdout=StringIO())
        with patch('builtins.input', return_value='no'), self.assertRaises(CommandError):
            call_command('bera_control_recover')
        with patch('builtins.input', return_value='BERA CONTROL'), patch('bera_control.management.commands.bera_control_recover.getpass', return_value=PASSWORD+'!'):
            call_command('bera_control_recover', stdout=StringIO())
        self.assertTrue(check_password(PASSWORD+'!', ControlState.objects.get(pk=1).password_hash))


@override_settings(BERA_LICENSE_ENFORCED_MODULES=('loyalty',), BERA_LICENSE_INSTALLATION_ID='no-remote-license')
class LocalActivationTests(TestCase):
    def setUp(self):
        initialize(issue_bootstrap(), PASSWORD)
        self.client.post('/bera-control/', {'password': PASSWORD})
        self.staff = get_user_model().objects.create_user('activation-manager', is_staff=True)
        self.customer = Client()
        self.customer.force_login(self.staff)

    def issue(self):
        response = self.client.post('/bera-control/modules/loyalty/code/')
        self.assertEqual(response.status_code, 302)
        return display_activation_code(ModuleActivation.objects.get(module='loyalty'))

    def redeem(self, code, module='loyalty'):
        return self.customer.post('/panel/moduli/aktiviraj-kod/', {'module': module, 'activation_code': code})

    @patch('bera_licensing.services.fetch_license')
    def test_owner_code_customer_activation_without_remote_configuration(self, remote):
        code = self.issue()
        self.assertTrue(code.startswith('BERA-'))
        self.assertFalse(module_enabled('loyalty'))
        self.assertContains(self.client.get('/bera-control/dashboard/'), code)
        response = self.customer.get('/panel/moduli/')
        self.assertContains(response, 'Aktivacija')
        self.assertContains(response, 'moduleActivationDialog')
        self.assertNotContains(response, code)
        self.assertNotContains(response, 'Installation ID')
        self.assertNotContains(response, 'Provjeri licencu sada')
        self.redeem(code.lower())
        self.assertTrue(module_enabled('loyalty'))
        self.assertIsNotNone(ModuleActivation.objects.get().activated_at)
        self.assertEqual(AuditEvent.objects.filter(action='module_activated').count(), 1)
        self.redeem(code)
        self.assertEqual(AuditEvent.objects.filter(action='module_activated').count(), 1)
        remote.assert_not_called()

    def test_wrong_code_wrong_module_and_disabled_owner_switch(self):
        code = self.issue()
        self.redeem('BERA-WRONG')
        self.redeem(code, 'newsletter')
        self.assertFalse(module_enabled('loyalty'))
        self.client.post('/bera-control/modules/loyalty/', {'enabled': 'off'})
        self.redeem(code)
        self.assertIsNone(ModuleActivation.objects.get().activated_at)
        self.client.post('/bera-control/modules/loyalty/', {'enabled': 'on'})
        self.redeem(code)
        self.assertTrue(module_enabled('loyalty'))
        self.client.post('/bera-control/modules/loyalty/', {'enabled': 'off'})
        self.assertFalse(module_enabled('loyalty'))
        self.client.post('/bera-control/modules/loyalty/', {'enabled': 'on'})
        self.assertTrue(module_enabled('loyalty'))

    def test_regeneration_revokes_old_code_and_entitlement(self):
        old = self.issue()
        self.redeem(old)
        self.assertTrue(module_enabled('loyalty'))
        new = self.issue()
        self.assertNotEqual(old, new)
        self.assertFalse(module_enabled('loyalty'))
        self.redeem(old)
        self.assertFalse(module_enabled('loyalty'))
        self.redeem(new)
        self.assertTrue(module_enabled('loyalty'))
        stored = ModuleActivation.objects.get()
        self.assertNotIn(new, stored.encrypted_code)
        self.assertNotEqual(new, stored.code_digest)

    def test_duration_starts_at_redemption_and_expiry_blocks_backend(self):
        now = timezone.now()
        self.client.post('/bera-control/modules/loyalty/code/', {'duration_days': 30})
        activation = ModuleActivation.objects.get()
        self.assertEqual(activation.duration_days, 30)
        self.assertIsNone(activation.expires_at)
        code = display_activation_code(activation)
        with patch('django.utils.timezone.now', return_value=now + timedelta(days=4)):
            self.redeem(code)
        activation.refresh_from_db()
        self.assertEqual(activation.expires_at, now + timedelta(days=34))
        with patch('django.utils.timezone.now', return_value=activation.expires_at - timedelta(seconds=1)):
            self.assertTrue(module_enabled('loyalty'))
        with patch('django.utils.timezone.now', return_value=activation.expires_at):
            self.assertFalse(module_enabled('loyalty'))
            with self.assertRaises(PermissionDenied):
                require_module('loyalty')
            self.assertEqual(self.customer.get('/loyalty/').status_code, 403)

    def test_red_badge_only_in_final_seven_days(self):
        now = timezone.now()
        code = self.issue()
        self.redeem(code)
        for remaining, label in [(timedelta(days=7, seconds=1), None),
                                 (timedelta(days=7), '7 dana do isteka licence'),
                                 (timedelta(days=3, hours=12), '4 dana do isteka licence'),
                                 (timedelta(hours=5), '1 dan do isteka licence')]:
            ModuleActivation.objects.filter(module='loyalty').update(expires_at=now + remaining)
            with patch('django.utils.timezone.now', return_value=now):
                response = self.customer.get('/panel/moduli/')
            if label:
                self.assertContains(response, label)
                self.assertContains(response, 'class="module-expiry-badge"')
            else:
                self.assertNotContains(response, 'class="module-expiry-badge"')
        ModuleActivation.objects.filter(module='loyalty').update(expires_at=now - timedelta(seconds=1))
        self.assertContains(self.customer.get('/panel/moduli/'), 'Licenca je istekla')
        ModuleActivation.objects.filter(module='loyalty').update(expires_at=None)
        self.assertNotContains(self.customer.get('/panel/moduli/'), 'class="module-expiry-badge"')

    def test_owner_can_set_pending_duration_and_renew_without_new_code(self):
        code = self.issue()
        self.client.post('/bera-control/modules/loyalty/duration/', {'duration_days': 15})
        self.assertIsNone(ModuleActivation.objects.get().expires_at)
        self.redeem(code)
        activation = ModuleActivation.objects.get()
        now = timezone.now()
        ModuleActivation.objects.filter(module='loyalty').update(expires_at=now - timedelta(days=1))
        with patch('django.utils.timezone.now', return_value=now):
            self.assertFalse(module_enabled('loyalty'))
            self.client.post('/bera-control/modules/loyalty/duration/', {'duration_days': 90})
            self.assertTrue(module_enabled('loyalty'))
        activation.refresh_from_db()
        self.assertEqual(activation.expires_at, now + timedelta(days=90))
        self.assertEqual(display_activation_code(activation), code)
        self.client.post('/bera-control/modules/loyalty/duration/', {'duration_days': ''})
        activation.refresh_from_db()
        self.assertIsNone(activation.expires_at)
        self.assertIsNone(activation.duration_days)
        self.assertTrue(AuditEvent.objects.filter(action='module_duration_changed').exists())

    def test_invalid_duration_cannot_mutate_license_and_staff_cannot_extend(self):
        code = self.issue()
        for value in ('-1', '0', '36501', '1.5', 'invalid'):
            self.client.post('/bera-control/modules/loyalty/code/', {'duration_days': value})
            self.client.post('/bera-control/modules/loyalty/duration/', {'duration_days': value})
            self.assertEqual(display_activation_code(ModuleActivation.objects.get()), code)
            self.assertIsNone(ModuleActivation.objects.get().duration_days)
        self.assertEqual(self.customer.post('/bera-control/modules/loyalty/duration/', {'duration_days': 30}).status_code, 302)
        self.assertIsNone(ModuleActivation.objects.get().duration_days)
        self.assertEqual(self.client.get('/bera-control/modules/loyalty/duration/').status_code, 405)

    def test_code_generation_is_owner_only_and_redemption_staff_only(self):
        self.assertEqual(self.customer.post('/bera-control/modules/loyalty/code/').status_code, 302)
        self.assertFalse(ModuleActivation.objects.exists())
        code = self.issue()
        self.assertEqual(Client().post('/panel/moduli/aktiviraj-kod/', {'module': 'loyalty', 'activation_code': code}).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        self.assertEqual(csrf_client.post('/panel/moduli/aktiviraj-kod/', {'module': 'loyalty', 'activation_code': code}).status_code, 403)
        self.assertEqual(self.client.get('/bera-control/modules/loyalty/code/').status_code, 405)
        self.assertEqual(self.customer.get('/panel/moduli/aktiviraj-kod/').status_code, 405)
        self.assertIsNone(ModuleActivation.objects.get().activated_at)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections, connections, OperationalError
from django.test import TransactionTestCase
import time


class ConcurrentBootstrapTests(TransactionTestCase):
    def test_exactly_one_initializer_wins(self):
        ControlState.objects.get_or_create(pk=1)
        token = issue_bootstrap()
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            barrier.wait(timeout=10)
            try:
                for attempt in range(20):
                    try:
                        initialize(token, PASSWORD)
                        return True
                    except PermissionDenied:
                        return False
                    except OperationalError as exc:
                        if 'locked' not in str(exc).lower() or attempt == 19:
                            raise
                        time.sleep(.02)
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(AuditEvent.objects.filter(action='initialized').count(), 1)


class ChatBotModuleTests(TestCase):
    initialize = ControlTests.initialize
    login = ControlTests.login

    def test_chatbot_is_connected_and_free_card_is_hidden(self):
        self.initialize()
        self.login()
        response = self.client.get('/bera-control/dashboard/')
        self.assertContains(response, 'ChatBot')
        self.assertContains(response, '/bera-control/modules/chatbot/')
        self.assertNotContains(response, 'FREE')
        self.assertNotContains(response, 'Regularna akcija')

    def test_chatbot_activation_and_switch_control_runtime(self):
        from shop.chatbot import public_config
        from shop.models import ChatBotSettings
        ChatBotSettings.objects.update_or_create(pk=1, defaults={'enabled': True})
        self.initialize()
        self.login()
        self.assertFalse(public_config()['enabled'])
        self.client.post('/bera-control/modules/chatbot/code/')
        activation = ModuleActivation.objects.get(module='chatbot')
        redeem_module_code('chatbot', display_activation_code(activation))
        self.assertTrue(public_config()['enabled'])
        self.client.post('/bera-control/modules/chatbot/', {'enabled': 'off'})
        self.assertFalse(public_config()['enabled'])
        response = self.client.post('/api/chatbot/', data='{"message":"Test"}', content_type='application/json')
        self.assertEqual(response.status_code, 404)
