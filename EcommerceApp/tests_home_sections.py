from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .forms import HomeProductSectionForm
from .models import HomeProductSection, Product
from .views import staff_home_sections


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class HomeSectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('home-admin', 'home@example.com', 'password')
        cls.product = Product.objects.create(naziv='Test stolica', sifra='HOME-1', cijena='25.00')

    def request(self, method='get', data=None, query=''):
        request = getattr(RequestFactory(), method)(reverse('staff_home_sections') + query, data or {})
        request.session = {}
        request.user = self.admin
        return request

    def test_create_edit_and_remove_products(self):
        form = HomeProductSectionForm({'naziv': 'Izdvojeni artikli', 'redoslijed': 2, 'aktivan': 'on', 'artikli': [self.product.pk]})
        self.assertTrue(form.is_valid(), form.errors)
        section = form.save()
        self.assertEqual(list(section.artikli.all()), [self.product])
        form = HomeProductSectionForm({'naziv': 'Nova ponuda', 'redoslijed': 0}, instance=section)
        self.assertTrue(form.is_valid(), form.errors)
        section = form.save()
        self.assertFalse(section.aktivan)
        self.assertFalse(section.artikli.exists())

    def test_invalid_product_is_rejected(self):
        form = HomeProductSectionForm({'naziv': 'Test', 'redoslijed': 0, 'artikli': ['99999999']})
        self.assertFalse(form.is_valid())
        self.assertIn('artikli', form.errors)

    def test_search_by_code(self):
        response = staff_home_sections(self.request(data={'search': '1', 'q': 'HOME-1'}))
        self.assertContains(response, 'Test stolica')

    def test_regular_staff_cannot_manage_sections(self):
        self.admin.is_superuser = False
        response = staff_home_sections(self.request())
        self.assertEqual(response.status_code, 302)

    @patch('EcommerceApp.views.messages.success')
    def test_delete_only_on_post(self, success):
        section = HomeProductSection.objects.create(naziv='Ponuda')
        response = staff_home_sections(self.request('post', {'action': 'delete', 'section_id': section.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(HomeProductSection.objects.filter(pk=section.pk).exists())

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_editor_renders(self, base_context):
        response = staff_home_sections(self.request())
        self.assertContains(response, 'Početna stranica')
        self.assertContains(response, 'product-search')
