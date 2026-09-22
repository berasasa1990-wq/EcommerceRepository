from html.parser import HTMLParser
from unittest.mock import patch

import requests
from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


class LoginFormMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_login = False
        self.has_widget = False
        self.has_csrf = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.in_login = attrs.get('id') == 'login-form'
        if self.in_login and attrs.get('class') == 'cf-turnstile':
            self.has_widget = True
        if self.in_login and attrs.get('name') == 'csrfmiddlewaretoken':
            self.has_csrf = True

    def handle_endtag(self, tag):
        if tag == 'form':
            self.in_login = False


@override_settings(
    DEBUG=False,
    TURNSTILE_SITE_KEY='test-public-site-key',
    TURNSTILE_SECRET_KEY='test-private-secret-key',
    SITE_PREP_ENABLED=False,
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class AdminTurnstileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('turnstile-admin', 'admin@example.com', 'test-password')

    def setUp(self):
        self.url = reverse('admin:login')
        self.payload = {'username': self.user.username, 'password': 'test-password',
                        'cf_turnstile_response': 'challenge-token', 'next': '/admin/'}
        patcher = patch('EcommerceApp.views.requests.post')
        self.siteverify = patcher.start()
        self.addCleanup(patcher.stop)
        self.siteverify.return_value.json.return_value = {'success': True}

    def assert_rejected(self, response, message):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, message)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertNotContains(response, 'test-private-secret-key')

    def test_existing_admin_url_renders_widget_inside_csrf_form(self):
        self.assertRedirects(self.client.get('/admin/'), self.url + '?next=/admin/', fetch_redirect_response=False)
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'admin/login.html')
        self.assertContains(response, 'data-sitekey="test-public-site-key"')
        self.assertContains(response, 'data-response-field-name="cf_turnstile_response"')
        self.assertContains(response, 'https://challenges.cloudflare.com/turnstile/v0/api.js', count=1)
        self.assertContains(response, 'storefront-admin.v20260916.css')
        self.assertNotContains(response, 'test-private-secret-key')
        parser = LoginFormMarkup()
        parser.feed(response.content.decode())
        self.assertTrue(parser.has_widget)
        self.assertTrue(parser.has_csrf)
        self.siteverify.assert_not_called()

    def test_missing_empty_and_oversized_tokens_are_rejected_without_network(self):
        for token in [None, '', '   ', 'x' * 2049]:
            with self.subTest(token_length=len(token or '')):
                data = dict(self.payload)
                if token is None:
                    data.pop('cf_turnstile_response')
                else:
                    data['cf_turnstile_response'] = token
                self.assert_rejected(self.client.post(self.url, data), 'Molimo potvrdite')
        self.siteverify.assert_not_called()

    def test_invalid_expired_reused_and_malformed_verification_is_rejected(self):
        for result in [{'success': False}, {'success': False, 'error-codes': ['timeout-or-duplicate']},
                       {}, {'success': 'true'}, {'success': 1}, None, []]:
            with self.subTest(result=result):
                self.siteverify.return_value.json.return_value = result
                self.assert_rejected(self.client.post(self.url, self.payload), 'Turnstile provjera nije uspjela')

    def test_network_errors_and_invalid_json_are_rejected(self):
        for failure in [requests.Timeout(), requests.ConnectionError()]:
            self.siteverify.side_effect = failure
            self.assert_rejected(self.client.post(self.url, self.payload), 'Turnstile provjera nije uspjela')
        self.siteverify.side_effect = None
        self.siteverify.return_value.json.side_effect = ValueError('invalid JSON')
        self.assert_rejected(self.client.post(self.url, self.payload), 'Turnstile provjera nije uspjela')

    def test_success_uses_existing_server_side_verifier_and_keys(self):
        response = self.client.post(self.url, self.payload, REMOTE_ADDR='192.0.2.10')
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))
        self.siteverify.assert_called_once_with(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data={'secret': 'test-private-secret-key', 'response': 'challenge-token', 'remoteip': '192.0.2.10'},
            timeout=10,
        )
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_missing_configuration_never_bypasses_challenge(self):
        for site, secret in [('', ''), ('test-public-site-key', ''), ('', 'test-private-secret-key')]:
            with self.subTest(site_present=bool(site), secret_present=bool(secret)):
                with override_settings(TURNSTILE_SITE_KEY=site, TURNSTILE_SECRET_KEY=secret):
                    self.assert_rejected(self.client.post(self.url, self.payload), 'Sigurnosna provjera prijave nije podešena')
        self.siteverify.assert_not_called()

    @override_settings(DEBUG=True, TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='')
    def test_debug_mode_allows_local_admin_login_without_turnstile(self):
        response = self.client.post(self.url, {
            'username': self.user.username,
            'password': 'test-password',
            'next': '/admin/',
        })
        self.assertRedirects(response, '/admin/', fetch_redirect_response=False)
        self.siteverify.assert_not_called()

    def test_password_staff_and_active_checks_remain_enforced(self):
        response = self.client.post(self.url, dict(self.payload, password='wrong'))
        self.assert_rejected(response, 'errornote')
        for changes in [{'is_staff': False}, {'is_staff': True, 'is_active': False}]:
            get_user_model().objects.filter(pk=self.user.pk).update(**changes)
            self.assert_rejected(self.client.post(self.url, self.payload), 'errornote')

    def test_csrf_is_required_even_with_valid_challenge(self):
        client = Client(enforce_csrf_checks=True)
        client.get(self.url)
        self.assertEqual(client.post(self.url, self.payload).status_code, 403)
        self.siteverify.assert_not_called()
        response = client.post(self.url, dict(self.payload, csrfmiddlewaretoken=client.cookies['csrftoken'].value))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.session[SESSION_KEY], str(self.user.pk))

    def test_external_next_url_is_not_used(self):
        response = self.client.post(self.url, dict(self.payload, next='https://example.org/'))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response['Location'].startswith('https://example.org'))

    def test_existing_staff_session_does_not_need_new_challenge(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.siteverify.assert_not_called()

    def test_registration_keeps_original_form_and_widget(self):
        from .forms import RegisterForm
        response = self.client.get(reverse('register'))
        self.assertIsInstance(response.context['form'], RegisterForm)
        self.assertContains(response, 'data-response-field-name="cf_turnstile_response"')
        self.assertContains(response, 'data-theme="light"')
        self.assertNotContains(response, 'test-private-secret-key')
