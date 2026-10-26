from django.contrib.auth import get_user_model
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse


class AuthCsrfTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = get_user_model().objects.create_user('csrf-customer', email='csrf@example.com', password='River!Stone-2934')

    def test_auth_pages_and_token_are_not_cached(self):
        for name in ['customer_login', 'customer_register', 'customer_csrf_token']:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
        token = self.client.get(reverse('customer_csrf_token')).json()['csrfToken']
        self.assertEqual(self.client.post(reverse('customer_csrf_token'), {'csrfmiddlewaretoken': token}).status_code, 405)

    def test_refresh_recovers_stale_login_token_without_disabling_csrf(self):
        login_url = reverse('customer_login')
        self.client.get(login_url)
        old = self.client.cookies['csrftoken'].value
        # Simulate rotation of the shared browser cookie after login in another tab.
        self.client.cookies['csrftoken'] = get_token(RequestFactory().get('/'))
        payload = {'username': self.user.email, 'password': 'River!Stone-2934', 'csrfmiddlewaretoken': old}
        self.assertEqual(self.client.post(login_url, payload).status_code, 403)
        payload['csrfmiddlewaretoken'] = self.client.get(reverse('customer_csrf_token')).json()['csrfToken']
        response = self.client.post(login_url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_refresh_does_not_allow_foreign_origin_or_missing_token(self):
        token = self.client.get(reverse('customer_csrf_token')).json()['csrfToken']
        payload = {'username': self.user.email, 'password': 'River!Stone-2934'}
        self.assertEqual(self.client.post(reverse('customer_login'), payload).status_code, 403)
        payload['csrfmiddlewaretoken'] = token
        self.assertEqual(self.client.post(reverse('customer_login'), payload, HTTP_ORIGIN='https://foreign.example').status_code, 403)
