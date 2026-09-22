from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='shop@example.com',
)
class PasswordResetFlowTests(TestCase):
    def test_request_page_has_a_fresh_csrf_token_and_is_not_cacheable(self):
        client = Client(enforce_csrf_checks=True)
        url = reverse('password_reset')

        response = client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertIn('no-store', response['Cache-Control'])
        csrf_token = client.cookies['csrftoken'].value
        response = client.post(url, {
            'email': 'nepostojeci@example.com',
            'csrfmiddlewaretoken': csrf_token,
        })
        self.assertRedirects(response, reverse('password_reset_done'), fetch_redirect_response=False)

    def test_request_sends_a_one_time_password_reset_link(self):
        user = get_user_model().objects.create_user(
            username='password-reset-user',
            email='kupac@example.com',
            password='Old-password-123!',
        )

        response = self.client.post(
            reverse('password_reset'), {'email': user.email}, follow=False,
        )

        self.assertRedirects(response, reverse('password_reset_done'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [user.email])
        self.assertIn('Promjena lozinke', mail.outbox[0].subject)
        self.assertIn('/lozinka/potvrdi/', mail.outbox[0].body)

    def test_new_password_page_uses_the_customer_auth_layout(self):
        user = get_user_model().objects.create_user(
            username='new-password-user',
            email='nova-lozinka@example.com',
            password='Old-password-123!',
        )
        url = reverse('password_reset_confirm', kwargs={
            'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
            'token': default_token_generator.make_token(user),
        })

        response = self.client.get(url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Postavite novu lozinku')
        self.assertContains(response, 'Potvrdite novu lozinku')
        self.assertContains(response, 'Sačuvaj novu lozinku')

    def test_unknown_email_has_the_same_confirmation_page(self):
        response = self.client.post(
            reverse('password_reset'), {'email': 'nepostojeci@example.com'}, follow=False,
        )

        self.assertRedirects(response, reverse('password_reset_done'), fetch_redirect_response=False)
        self.assertEqual(mail.outbox, [])
