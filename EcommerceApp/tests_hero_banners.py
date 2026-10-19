from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .forms import HeroBannerFormSet
from .models import HeroBanner, SiteSettings
from .views import staff_hero_settings


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class HeroBannerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.site = SiteSettings.load()
        self.factory = RequestFactory()

    def post_data(self):
        return {
            **{name: getattr(self.site, name) for name in
               ['hero_oznaka', 'hero_naslov', 'hero_podnaslov', 'hero_opis', 'hero_link']},
            'banners-TOTAL_FORMS': '1', 'banners-INITIAL_FORMS': '0',
            'banners-MIN_NUM_FORMS': '0', 'banners-MAX_NUM_FORMS': '20',
            'banners-0-hero_opis': 'Nova ponuda', 'banners-0-hero_link': 'https://example.com/ponuda/',
            'banners-0-redoslijed': '2', 'banners-0-aktivan': 'on',
        }

    def post(self, data):
        request = self.factory.post(reverse('staff_hero_settings'), data)
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)
        with patch('EcommerceApp.views.messages.success'), patch('EcommerceApp.views._base_context', return_value={'site_settings': self.site}), patch('EcommerceApp.views.render', return_value=HttpResponse('form errors')):
            return staff_hero_settings(request)

    def test_create_update_and_delete_banner(self):
        data = self.post_data()
        self.assertEqual(self.post(data).status_code, 302)
        banner = HeroBanner.objects.get()
        self.assertEqual(banner.hero_opis, 'Nova ponuda')
        data.update({'banners-INITIAL_FORMS': '1', 'banners-0-id': str(banner.pk), 'banners-0-hero_opis': 'Izmijenjen opis'})
        self.assertEqual(self.post(data).status_code, 302)
        banner.refresh_from_db()
        self.assertEqual(banner.hero_opis, 'Izmijenjen opis')
        data['banners-0-DELETE'] = 'on'
        self.assertEqual(self.post(data).status_code, 302)
        self.assertFalse(HeroBanner.objects.exists())

    def test_invalid_additional_link_prevents_all_saves(self):
        data = self.post_data()
        data['hero_naslov'] = 'Ne smije se snimiti'
        data['banners-0-hero_link'] = 'javascript:alert(1)'
        response = self.post(data)
        self.assertEqual(response.status_code, 200)
        self.site.refresh_from_db()
        self.assertNotEqual(self.site.hero_naslov, 'Ne smije se snimiti')
        self.assertFalse(HeroBanner.objects.exists())

    def test_single_and_multiple_banner_rendering(self):
        html = render_to_string('partials/home_fishing_hero.html', {'site_settings': self.site, 'hero_banners': [self.site]})
        self.assertNotIn('data-hero-next', html)
        banner = HeroBanner.objects.create(settings=self.site, hero_opis='Drugi opis', hero_slika='site/hero/test.png')
        html = render_to_string('partials/home_fishing_hero.html', {'site_settings': self.site, 'hero_banners': [self.site, banner]})
        self.assertEqual(html.count('data-hero-slide '), 2)
        self.assertEqual(html.count('<h1 '), 1)
        self.assertIn(' hidden', html)
        self.assertNotIn('data-hero-next', html)
        self.assertNotIn('data-hero-prev', html)
        self.assertNotIn('data-hero-index', html)
        self.assertNotIn('data-hero-pause', html)
        self.assertIn('Drugi opis', html)
        self.assertIn('site/hero/test.png', html)
        self.assertEqual(html.count('href="' + self.site.hero_link + '"'), 4)

    def test_active_banners_order_and_formset_limit(self):
        HeroBanner.objects.create(settings=self.site, hero_opis='Treći', redoslijed=3)
        HeroBanner.objects.create(settings=self.site, hero_opis='Drugi', redoslijed=1)
        HeroBanner.objects.create(settings=self.site, hero_opis='Sakriven', aktivan=False)
        self.assertEqual(list(self.site.hero_banners.filter(aktivan=True).values_list('hero_opis', flat=True)), ['Drugi', 'Treći'])
        formset = HeroBannerFormSet(data={'banners-TOTAL_FORMS': '21', 'banners-INITIAL_FORMS': '0'}, instance=self.site, prefix='banners')
        self.assertFalse(formset.is_valid())
        self.assertTrue(formset.non_form_errors())

    def test_settings_page_renders_full_formset(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create(username='hero-owner', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        response = self.client.get(reverse('staff_hero_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="addHeroBanner"')
        self.assertContains(response, 'name="banners-TOTAL_FORMS"')
        self.assertContains(response, 'hero-settings__layout')
        self.assertContains(response, 'name="banners-__prefix__-hero_opis"')

    def test_home_includes_active_banners_only(self):
        HeroBanner.objects.create(settings=self.site, hero_opis='Aktivna hero ponuda')
        HeroBanner.objects.create(settings=self.site, hero_opis='Sakrivena hero ponuda', aktivan=False)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Aktivna hero ponuda')
        self.assertNotContains(response, 'Sakrivena hero ponuda')
        self.assertContains(response, 'hero-slider.js')
