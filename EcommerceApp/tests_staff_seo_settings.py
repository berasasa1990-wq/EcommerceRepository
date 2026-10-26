from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import PageSEO, SiteSettings


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class StaffSEOSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        PageSEO.objects.all().delete()
        self.site = SiteSettings.load()
        self.site.hero_link = 'https://example.com/banner/'
        self.site.save(update_fields=['hero_link'])
        self.admin = get_user_model().objects.create_superuser('seo-admin', 'seo@example.com', 'test-only')
        self.client.force_login(self.admin)
        self.url = reverse('staff_hero_settings')

    def test_page_lists_existing_seo_and_does_not_create_rows_on_get(self):
        PageSEO.objects.create(page_key='home', seo_title='Naslov početne')
        response = self.client.get(self.url)
        self.assertContains(response, 'Naslov početne')
        self.assertContains(response, 'Sačuvaj opće SEO postavke')
        self.assertEqual(PageSEO.objects.count(), 1)

    def test_page_save_updates_public_metadata_and_preserves_banners(self):
        response = self.client.post(self.url, {
            'action': 'page_seo', 'page_key': 'home', 'seo_title': 'Ribolovna oprema',
            'meta_description': 'Nova ponuda pribora za ribolov.', 'h1_naslov': 'Naša ponuda',
            'seo_tekst_iznad': 'Tekst iznad', 'seo_tekst_ispod': 'Tekst ispod',
        })
        self.assertRedirects(response, self.url + '?page_key=home#page-seo')
        self.site.refresh_from_db()
        self.assertEqual(self.site.hero_link, 'https://example.com/banner/')
        self.assertEqual(PageSEO.objects.get(page_key='home').h1_naslov, 'Naša ponuda')
        home = self.client.get(reverse('home'))
        self.assertContains(home, '<title>Ribolovna oprema</title>', html=True)
        self.assertContains(home, '<meta name="description" content="Nova ponuda pribora za ribolov.">', html=True)

    def test_invalid_title_is_not_saved_and_errors_are_shown(self):
        response = self.client.post(self.url, {'action': 'page_seo', 'page_key': 'home', 'seo_title': 'x' * 71})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['page_seo_form'].errors)
        self.assertFalse(PageSEO.objects.filter(page_key='home').exists())

    def test_general_seo_save_preserves_banner_and_page_overrides(self):
        PageSEO.objects.create(page_key='home', seo_title='Poseban naslov')
        response = self.client.post(self.url, {
            'action': 'site_seo', 'seo_title': 'Zadani naslov', 'meta_description': 'Zadani opis',
            'seo_email': 'shop@example.com', 'seo_drzava': 'BA',
        })
        self.assertRedirects(response, self.url)
        self.site.refresh_from_db()
        self.assertEqual(self.site.seo_title, 'Zadani naslov')
        self.assertEqual(self.site.hero_link, 'https://example.com/banner/')
        self.assertEqual(PageSEO.objects.get(page_key='home').seo_title, 'Poseban naslov')

    def test_unknown_page_is_rejected(self):
        self.assertEqual(self.client.post(self.url, {'action': 'page_seo', 'page_key': 'unknown'}).status_code, 404)

    def test_regular_user_cannot_edit_seo(self):
        user = get_user_model().objects.create_user('customer', password='test-only')
        self.client.force_login(user)
        response = self.client.post(self.url, {'action': 'page_seo', 'page_key': 'home', 'seo_title': 'Blocked'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PageSEO.objects.filter(page_key='home').exists())
