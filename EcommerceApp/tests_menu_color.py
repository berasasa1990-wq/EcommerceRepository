from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from .models import SiteSettings


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class MenuColorTests(TestCase):
    def setUp(self):
        self.site = SiteSettings.load()
        self.user = get_user_model().objects.create_superuser('colors', 'colors@example.com', 'test')

    def test_white_default_and_switching_invalidates_cached_settings(self):
        self.assertEqual(self.site.boja_menija, 'white')
        for color in ['white', 'black', 'white']:
            self.site.boja_menija = color
            self.site.save()
            response = self.client.get(reverse('home'))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'data-menu-color="{color}"')
            self.assertContains(response, 'css/menu-color.v20260909.css')
            self.assertEqual(SiteSettings.load().boja_menija, color)

    def test_admin_shows_choice_and_saves_only_valid_colors(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('admin:EcommerceApp_sitesettings_change', args=[self.site.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="boja_menija"')
        self.assertContains(response, 'Crna')
        self.assertContains(response, 'Bijela')
        request = RequestFactory().post('/')
        request.user = self.user
        form_class = admin.site._registry[SiteSettings].get_form(request, self.site, fields=('boja_menija',))
        form = form_class(data={'boja_menija': 'black'}, instance=self.site)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.site.refresh_from_db()
        self.assertEqual(self.site.boja_menija, 'black')
        form = form_class(data={'boja_menija': 'invalid'}, instance=self.site)
        self.assertFalse(form.is_valid())
        self.assertIn('boja_menija', form.errors)
