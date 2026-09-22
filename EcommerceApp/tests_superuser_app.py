from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SuperuserAppTests(TestCase):
    def setUp(self):
        self.url = reverse('superuser_app')
        self.superuser = get_user_model().objects.create_superuser(
            'app-owner', 'owner@example.com', 'test-password',
        )
        self.staff = get_user_model().objects.create_user(
            'app-staff', 'staff@example.com', 'test-password', is_staff=True,
        )

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_staff_user_is_forbidden(self):
        self.client.force_login(self.staff)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'staff/superuser_app_denied.html')
        self.assertContains(response, 'stranica je namijenjena samo superuserima', status_code=403)

    def test_superuser_can_open_the_app_dashboard(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'staff/superuser_app.html')

    def test_product_lookup_is_read_only_and_returns_price_and_stock(self):
        product = Product.objects.create(naziv='Test štap', sifra='STAP-1', cijena='99.90', stanje=4)
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('superuser_app_products'), {'q': product.sifra})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test štap')
        self.assertContains(response, '99.90 KM')
        self.assertContains(response, '4 kom.')
