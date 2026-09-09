from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from .models import SiteSettings


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CategoryLogoTests(TestCase):
    def test_additional_logos_removed_from_site_and_settings(self):
        site = SiteSettings.load()
        site.logo_glavni_sajt = 'site/old-parent.jpg'
        site.logo_desno_kategorije = 'site/old-right.jpg'
        site.save()
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'site/old-parent.jpg')
        self.assertNotContains(response, 'site/old-right.jpg')
        self.assertNotContains(response, 'class="categories-brand-logo"')
        self.assertContains(response, 'site-logo-compact.v20260912.css')
        request = RequestFactory().get('/')
        request.user = get_user_model().objects.create_superuser('logo', 'logo@example.com', 'test')
        form = admin.site._registry[SiteSettings].get_form(request, site)
        self.assertIn('logo', form.base_fields)
        self.assertNotIn('logo_glavni_sajt', form.base_fields)
        self.assertNotIn('logo_desno_kategorije', form.base_fields)
