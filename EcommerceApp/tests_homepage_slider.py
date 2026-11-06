from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from .models import SiteSettings, HeroBanner
from .tests_hero_banners import upload
from .forms import HomepageSlideForm


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class HomepageSliderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.site = SiteSettings.load()

    def test_duplicate_image_with_different_name_is_rejected(self):
        HeroBanner.objects.create(settings=self.site, hero_slika=upload())
        duplicate = upload()
        duplicate.name = 'different.png'
        form = HomepageSlideForm(data={'aktivan': True, 'redoslijed': 0}, files={'hero_slika': duplicate},
                                 instance=HeroBanner(settings=self.site))
        self.assertFalse(form.is_valid())
        self.assertIn('hero_slika', form.errors)

    def test_main_banner_cannot_duplicate_an_existing_slide(self):
        from .forms import HeroBannerSettingsForm
        HeroBanner.objects.create(settings=self.site, hero_slika=upload())
        form = HeroBannerSettingsForm(data={'hero_link': ''}, files={'hero_slika': upload()}, instance=self.site)
        self.assertFalse(form.is_valid())
        self.assertIn('hero_slika', form.errors)

    def test_duplicate_of_main_banner_is_rejected(self):
        self.site.hero_slika = upload()
        self.site.save()
        with self.assertRaises(ValidationError):
            HeroBanner.objects.create(settings=self.site, hero_slika=upload())

    def test_only_first_image_is_requested_initially_and_inactive_is_omitted(self):
        first = HeroBanner.objects.create(settings=self.site, hero_slika=upload())
        second = HeroBanner.objects.create(settings=self.site, hero_slika=upload(textured=True))
        inactive = HeroBanner.objects.create(settings=self.site, hero_slika=upload((100, 150), textured=True), aktivan=False)
        html = render_to_string('partials/home_uploaded_banner.html', {'site_settings': self.site})
        self.assertIn('src="'+first.hero_slika.url+'" fetchpriority="high"', html)
        self.assertIn('data-slide-src="'+second.hero_slika.url+'"', html)
        self.assertNotIn(inactive.hero_slika.url, html)
        self.assertEqual(html.count('data-home-slide hidden'), 1)
        self.assertLessEqual(first.hero_slika.size, 80000)

    def test_staff_can_add_edit_and_delete_single_image_slide(self):
        self.client.force_login(get_user_model().objects.create_superuser('slides', 'slides@example.com', 'password'))
        url = reverse('staff_banners')
        response = self.client.post(url+'?add=slide', {'hero_slika': upload(), 'hero_link': '', 'redoslijed': 1, 'aktivan': 'on'})
        self.assertEqual(response.status_code, 302)
        slide = HeroBanner.objects.get(settings=self.site)
        response = self.client.get(url+f'?edit=slide-{slide.pk}')
        self.assertContains(response, 'name="hero_slika"')
        self.assertNotContains(response, 'name="slika_mobilna"')
        response = self.client.post(url, {'delete_banner': f'slide-{slide.pk}'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(HeroBanner.objects.exists())

    def test_mobile_image_is_capped_and_hidden_picture_uses_empty_source(self):
        self.site.hero_slika_mobilna = upload((1080, 1350), textured=True)
        self.site.hero_video_mobilni = 'site/mobile/test.mp4'
        self.site.save()
        self.assertLessEqual(self.site.hero_slika_mobilna.size, 200000)
        html = render_to_string('partials/home_uploaded_banner.html', {'site_settings': self.site})
        self.assertIn('media="(max-width: 767px)" srcset="data:image/gif;', html)
