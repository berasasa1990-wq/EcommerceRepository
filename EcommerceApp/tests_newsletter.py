from django.test import TestCase, Client
from django.urls import reverse
from .models import MarketingSubscriber


class NewsletterTests(TestCase):
    def test_subscription_and_repeat(self):
        url = reverse('newsletter_subscribe')
        for email in [' Newsletter-Test@example.com ', 'newsletter-test@example.com']:
            response = self.client.post(url, {'email': email})
            self.assertTrue(response.json()['ok'])
        self.assertEqual(MarketingSubscriber.objects.count(), 1)
        self.assertTrue(MarketingSubscriber.objects.get(email='newsletter-test@example.com').aktivan)

    def test_inactive_subscription_is_reactivated(self):
        sub = MarketingSubscriber.objects.create(email='inactive@example.com', aktivan=False)
        self.assertTrue(self.client.post(reverse('newsletter_subscribe'), {'email': sub.email}).json()['ok'])
        sub.refresh_from_db()
        self.assertTrue(sub.aktivan)

    def test_invalid_emails_do_not_create_subscriptions(self):
        for email in ['', 'bad@@example.com', 'a b@example.com', 'not-an-email']:
            self.assertEqual(self.client.post(reverse('newsletter_subscribe'), {'email': email}).status_code, 400)
        self.assertFalse(MarketingSubscriber.objects.exists())

    def test_post_and_csrf_required(self):
        url = reverse('newsletter_subscribe')
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post(url, {'email': 'test@example.com'}).status_code, 403)
        self.assertFalse(MarketingSubscriber.objects.exists())


class NewsletterPanelTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.staff = get_user_model().objects.create_user(username='newsletter-staff', is_staff=True)
        self.url = reverse('staff_newsletter')

    def test_panel_link_and_only_active_subscribers_are_shown(self):
        self.client.force_login(self.staff)
        MarketingSubscriber.objects.create(email='active@example.com', aktivan=True)
        MarketingSubscriber.objects.create(email='inactive@example.com', aktivan=False)
        self.assertContains(self.client.get(reverse('staff_panel')), self.url)
        response = self.client.get(self.url)
        self.assertContains(response, 'active@example.com')
        self.assertNotContains(response, 'inactive@example.com')
        self.assertIn('no-store', response['Cache-Control'])

    def test_search_and_pagination(self):
        self.client.force_login(self.staff)
        MarketingSubscriber.objects.bulk_create([MarketingSubscriber(email=f'panel-{i}@example.com') for i in range(26)])
        response = self.client.get(self.url)
        self.assertEqual(len(response.context['subscribers']), 25)
        self.assertEqual(len(self.client.get(self.url, {'page': 2}).context['subscribers']), 1)
        response = self.client.get(self.url, {'q': 'panel-12@'})
        self.assertEqual(response.context['subscribers'].paginator.count, 1)
        self.assertContains(response, 'panel-12@example.com')

    def test_anonymous_and_regular_users_cannot_view_email_list(self):
        from django.contrib.auth import get_user_model
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username='newsletter-customer'))
        self.assertEqual(self.client.get(self.url).status_code, 302)
