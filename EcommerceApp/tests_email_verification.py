import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .account_verification import verification_token_generator
from .models import UserProfile


@override_settings(TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='', SITE_PREP_ENABLED=False,
    SECURE_SSL_REDIRECT=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailVerificationTests(TestCase):
    def setUp(self):
        self.payload = {'ime_prezime': 'Novi Kupac', 'email': 'verify@example.com',
                        'lozinka': 'Password-123!', 'lozinka_potvrda': 'Password-123!'}

    def register(self):
        response = self.client.post(reverse('register'), self.payload)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.user = User.objects.get(username=self.payload['email'])
        return re.search(r'https?://\S+', mail.outbox[-1].body).group(0)

    def test_email_link_is_required_before_login_and_consumed_after_activation(self):
        link = self.register()
        self.assertEqual(mail.outbox[-1].to, [self.payload['email']])
        self.assertFalse(self.user.is_active)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.post(reverse('login'), self.payload).status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertRedirects(self.client.get(link), reverse('login'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.post(reverse('login'), self.payload).status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        # A previously consumed link cannot reactivate a later disabled account.
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertRedirects(self.client.get(link), reverse('register'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_invalid_expired_and_password_reset_tokens_cannot_activate(self):
        self.register()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        reset_token = default_token_generator.make_token(self.user)
        with patch.object(verification_token_generator, '_now', return_value=timezone.now().replace(tzinfo=None) - timedelta(days=4)):
            expired = verification_token_generator.make_token(self.user)
        for token in ('invalid-token', reset_token, expired):
            self.assertRedirects(self.client.get(reverse('activate', args=[uid, token])),
                                 reverse('register'), fetch_redirect_response=False)
            self.user.refresh_from_db()
            self.assertFalse(self.user.is_active)

    def test_smtp_failure_rolls_back_account_and_does_not_log_in(self):
        with patch('EcommerceApp.account_verification.send_mail', side_effect=RuntimeError('SMTP failed')):
            response = self.client.post(reverse('register'), self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email za potvrdu nije poslan.')
        self.assertFalse(User.objects.filter(email=self.payload['email']).exists())
        self.assertFalse(UserProfile.objects.filter(user__email=self.payload['email']).exists())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_existing_active_accounts_keep_login_access(self):
        user = User.objects.create_user('existing', email=self.payload['email'], password=self.payload['lozinka'])
        response = self.client.post(reverse('login'), self.payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
