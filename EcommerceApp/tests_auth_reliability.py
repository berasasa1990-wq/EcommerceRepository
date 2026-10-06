from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .forms import LoginForm, RegisterForm
from .models import UserProfile


@override_settings(
    TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='', SITE_PREP_ENABLED=False,
    SECURE_SSL_REDIRECT=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class AuthReliabilityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = '  Password-123!  '
        self.payload = {
            'ime_prezime': 'Test Kupac', 'email': 'customer@example.com',
            'lozinka': self.password, 'lozinka_potvrda': self.password,
        }

    def user(self, username='customer', **kwargs):
        return User.objects.create_user(
            username=username, email=self.payload['email'],
            password=self.password, **kwargs,
        )

    def test_login_uses_customer_after_manual_record_with_same_email(self):
        manual = User.objects.create_user('manual', email=self.payload['email'])
        self.assertFalse(manual.has_usable_password())
        customer = self.user()
        response = self.client.post(reverse('login'), self.payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), customer.pk)
        account = self.client.get(reverse('account'))
        self.assertEqual(account.status_code, 200)
        self.assertIn('no-store', account['Cache-Control'])
        self.assertEqual(account.wsgi_request.user.pk, customer.pk)

    def test_login_skips_inactive_merged_record(self):
        self.user('merged', is_active=False)
        customer = self.user()
        form = LoginForm(self.payload)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.user.pk, customer.pk)

    def test_login_matches_password_instead_of_first_active_duplicate(self):
        first = self.user('first')
        first.set_password('different-password')
        first.save()
        customer = self.user()
        form = LoginForm(self.payload)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.user.pk, customer.pk)

    def test_ambiguous_duplicate_password_is_rejected(self):
        self.user('first')
        self.user('second')
        self.assertFalse(LoginForm(self.payload).is_valid())

    def test_wrong_password_and_inactive_account_cannot_login(self):
        self.user(is_active=False)
        self.assertFalse(LoginForm(self.payload).is_valid())
        self.assertFalse(LoginForm(dict(self.payload, lozinka='incorrect')).is_valid())

    def test_registration_preserves_password_and_waits_for_verification(self):
        response = self.client.post(reverse('register'), self.payload)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username=self.payload['email'])
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.check_password(self.password.strip()))
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertFalse(user.is_active)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(RegisterForm(self.payload).is_valid())

    def test_password_confirmation_does_not_ignore_spaces(self):
        form = RegisterForm(dict(self.payload, lozinka_potvrda=self.password.strip()))
        self.assertFalse(form.is_valid())
        self.assertIn('lozinka_potvrda', form.errors)

    def test_registration_rejects_values_exceeding_database_limits(self):
        form = RegisterForm(dict(self.payload, ime_prezime='A' * 151,
                                 email=('a' * 60 + '.') * 2 + 'b' * 30 + '@example.com'))
        self.assertFalse(form.is_valid())
        self.assertIn('ime_prezime', form.errors)
        self.assertIn('email', form.errors)

    def test_registration_rolls_back_partial_account(self):
        with patch('EcommerceApp.views.UserProfile.objects.create', side_effect=IntegrityError('test')):
            with self.assertLogs('EcommerceApp.views', level='ERROR'):
                response = self.client.post(reverse('register'), self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email=self.payload['email']).exists())
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, 'Nalog nije kreiran.')

    def test_auth_pages_are_not_cacheable_and_include_refresh(self):
        for name in ('login', 'register'):
            response = self.client.get(reverse(name))
            self.assertIn('no-store', response['Cache-Control'])
            self.assertContains(response, 'data-csrf-refresh-url=')
            self.assertContains(response, 'js/csrf-session.js')

    def test_protocol_relative_redirect_cannot_leave_site(self):
        self.user()
        response = self.client.post(reverse('login'), dict(self.payload, next='//evil.example/'))
        self.assertEqual(response['Location'], reverse('account'))
        self.client.logout()
        payload = dict(self.payload, email='new@example.com', next='//evil.example/')
        response = self.client.post(reverse('register'), payload)
        self.assertEqual(response['Location'], reverse('login'))

    def test_real_csrf_rotation_recovery_keeps_protection(self):
        self.user()
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('login'))
        old_token = client.cookies['csrftoken'].value
        response = client.post(reverse('login'), dict(self.payload, csrfmiddlewaretoken=old_token))
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(client.cookies['csrftoken'].value, old_token)
        client.get(reverse('logout'))
        response = client.post(reverse('login'), dict(self.payload, csrfmiddlewaretoken=old_token))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'Zahtjev nije obrađen.', status_code=403)
        self.assertIn('no-store', response['Cache-Control'])
        token_response = client.get(reverse('auth_csrf_token'))
        self.assertIn('no-store', token_response['Cache-Control'])
        response = client.post(reverse('login'), dict(self.payload, csrfmiddlewaretoken=token_response.json()['csrfToken']))
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', client.session)

    def test_missing_token_and_cross_origin_are_still_rejected(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse('register'), self.payload).status_code, 403)
        token = client.get(reverse('auth_csrf_token')).json()['csrfToken']
        response = client.post(reverse('register'), dict(self.payload, csrfmiddlewaretoken=token), HTTP_ORIGIN='https://evil.example')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email=self.payload['email']).exists())

    @override_settings(TURNSTILE_SECRET_KEY='orphan-secret')
    def test_incomplete_turnstile_configuration_does_not_block_login(self):
        self.user()
        with patch('EcommerceApp.views.verify_turnstile') as verify:
            response = self.client.post(reverse('login'), self.payload)
        self.assertEqual(response.status_code, 302)
        verify.assert_not_called()
