"""Role boundaries for the staff panel and Django model administration."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(DEBUG=True, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PanelAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.customer = User.objects.create_user('panel-customer', password='fixture-only-123')
        cls.staff = User.objects.create_user('panel-staff', password='fixture-only-123', is_staff=True)
        cls.owner = User.objects.create_superuser('panel-owner', 'owner@example.invalid', 'fixture-only-123')

    def test_anonymous_and_customer_cannot_open_panel(self):
        self.assertEqual(self.client.get('/panel').status_code, 302)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get('/panel').status_code, 302)

    def test_staff_can_open_panel_but_not_django_admin(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/panel').status_code, 200)
        for path in ('/admin/', reverse('admin:auth_user_changelist'),
                     '/admin/EcommerceApp/product/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/admin/login/', response.url)

    def test_staff_credentials_are_rejected_by_admin_login(self):
        response = self.client.post(reverse('admin:login'), {
            'username': self.staff.username, 'password': 'fixture-only-123', 'next': '/admin/',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_superuser_can_open_panel_and_django_admin(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/panel').status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code, 200)

    def test_superuser_can_log_in_through_admin_form(self):
        response = self.client.post(reverse('admin:login'), {
            'username': self.owner.username, 'password': 'fixture-only-123', 'next': '/admin/',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/admin/')
        self.assertEqual(self.client.get('/admin/').status_code, 200)
