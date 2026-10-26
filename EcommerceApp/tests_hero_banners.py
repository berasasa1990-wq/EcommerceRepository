from io import BytesIO
from unittest.mock import patch

from PIL import Image, ImageFilter
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .forms import HeroBannerSettingsForm
from .models import SiteSettings
from .utils.images import HOMEPAGE_BANNER_MAX_BYTES, prepare_homepage_banner


def upload(size=(2000, 750), textured=False):
    image = (Image.effect_noise(size, 15).filter(ImageFilter.GaussianBlur(2)).convert('RGB')
             if textured else Image.new('RGB', size, '#2479b2'))
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return SimpleUploadedFile('banner.png', buffer.getvalue(), content_type='image/png')


class HomepageBannerEncodingTests(SimpleTestCase):
    def test_full_size_and_hard_byte_limit_for_large_upload(self):
        result = prepare_homepage_banner(upload((2400, 900), textured=True))
        self.assertLessEqual(result.size, HOMEPAGE_BANNER_MAX_BYTES)
        with Image.open(result) as image:
            self.assertEqual(image.size, (1600, 600))
            self.assertIn(image.format, ('WEBP', 'AVIF'))

    def test_wrong_ratio_is_fitted_to_exact_dimensions(self):
        result = prepare_homepage_banner(upload((1000, 1000)))
        with Image.open(result) as image:
            self.assertEqual(image.size, (1600, 600))

    def test_optimized_file_is_not_lossily_encoded_twice(self):
        first = prepare_homepage_banner(upload())
        second = prepare_homepage_banner(first)
        self.assertEqual(second.read(), first.read())

    def test_white_background_is_preserved(self):
        buffer = BytesIO()
        Image.new('RGB', (1600, 600), 'white').save(buffer, format='PNG')
        result = prepare_homepage_banner(SimpleUploadedFile('white.png', buffer.getvalue()))
        with Image.open(result) as image:
            self.assertEqual(image.convert('RGB').getpixel((0, 0)), (255, 255, 255))

    def test_invalid_file_is_rejected(self):
        with self.assertRaises(ValidationError):
            prepare_homepage_banner(SimpleUploadedFile('bad.png', b'not an image'))

    @patch('EcommerceApp.utils.images.HOMEPAGE_BANNER_MAX_BYTES', 1)
    def test_impossible_budget_is_rejected_instead_of_saving_raw_upload(self):
        with self.assertRaises(ValidationError):
            prepare_homepage_banner(upload())


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class HomepageBannerUploadTests(TestCase):
    def setUp(self):
        cache.clear()
        self.site = SiteSettings.load()
        admin = get_user_model().objects.create_superuser('banner-admin', 'banner@example.com', 'test-only')
        self.client.force_login(admin)

    def test_upload_save_and_clear(self):
        response = self.client.post(reverse('staff_hero_settings'), {
            'hero_slika': upload(), 'hero_link': 'https://example.com/ponuda/',
        })
        self.assertEqual(response.status_code, 302)
        self.site.refresh_from_db()
        self.assertLessEqual(self.site.hero_slika.size, 80_000)
        with Image.open(self.site.hero_slika) as image:
            self.assertEqual(image.size, (1600, 600))
        html = render_to_string('partials/home_uploaded_banner.html', {'site_settings': self.site})
        self.assertIn('width="1600" height="600"', html)
        self.assertIn('https://example.com/ponuda/', html)
        self.assertNotIn('fishing-hero', html)
        self.assertNotIn('<h1', html)
        response = self.client.post(reverse('staff_hero_settings'), {'hero_slika-clear': 'on', 'hero_link': ''})
        self.assertEqual(response.status_code, 302)
        self.site.refresh_from_db()
        self.assertFalse(self.site.hero_slika)
        self.assertEqual(render_to_string('partials/home_uploaded_banner.html', {'site_settings': self.site}).strip(), '')

    def test_model_uploads_are_also_optimized(self):
        self.site.hero_slika = upload()
        self.site.save(update_fields=['hero_slika'])
        self.assertLessEqual(self.site.hero_slika.size, 80_000)
        with Image.open(self.site.hero_slika) as image:
            self.assertEqual(image.size, (1600, 600))

    @patch('EcommerceApp.utils.images.prepare_homepage_banner', side_effect=ValidationError('Cannot optimize'))
    def test_model_does_not_fall_back_to_unoptimized_upload(self, optimize):
        self.site.hero_slika = upload()
        with self.assertRaises(ValidationError):
            self.site.save(update_fields=['hero_slika'])
        self.site.refresh_from_db()
        self.assertFalse(self.site.hero_slika)

    def test_form_is_image_and_optional_link_only(self):
        form = HeroBannerSettingsForm({'hero_link': ''}, instance=self.site)
        self.assertEqual(list(form.fields), ['hero_slika', 'hero_link', 'bestsellers_banner_slika', 'bestsellers_banner_link'])
        self.assertTrue(form.is_valid(), form.errors)

    def test_settings_page_has_no_old_text_fields_or_slider(self):
        response = self.client.get(reverse('staff_hero_settings'))
        self.assertContains(response, '1600 × 600')
        self.assertNotContains(response, 'name="hero_naslov"')
        self.assertNotContains(response, 'name="banners-TOTAL_FORMS"')
        self.assertNotContains(response, 'id="addHeroBanner"')

    def test_empty_homepage_has_no_old_banner_or_slider(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="fishing-hero"')
        self.assertNotContains(response, 'hero-slider.js')

    def test_homepage_loads_one_banner_and_one_variant_of_each_css_bundle(self):
        from html.parser import HTMLParser

        class Resources(HTMLParser):
            def __init__(self):
                super().__init__()
                self.styles = []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'link' and attrs.get('rel') == 'stylesheet':
                    self.styles.append(attrs.get('href', ''))

        self.site.hero_slika = upload()
        self.site.save(update_fields=['hero_slika'])
        for authenticated in (True, False):
            if not authenticated:
                self.client.logout()
            response = self.client.get(reverse('home'))
            self.assertContains(response, 'class="home-uploaded-banner"', count=1)
            for obsolete in ('home-fishing-hero.css', 'hero-slider.js', 'hero-settings.js', 'class="hero-carousel"', 'class="fishing-hero"'):
                self.assertNotContains(response, obsolete)
            resources = Resources()
            resources.feed(response.content.decode())
            for bundle in ('storefront-core', 'storefront-theme'):
                self.assertEqual(sum(bundle in href for href in resources.styles), 1)
            self.assertEqual(len(resources.styles), len(set(resources.styles)))

    def test_bestsellers_upload_is_independent_and_can_be_cleared(self):
        self.site.hero_slika = upload()
        self.site.save(update_fields=['hero_slika'])
        original_name = self.site.hero_slika.name
        response = self.client.post(reverse('staff_hero_settings'), {
            'hero_link': 'https://example.com/home/',
            'bestsellers_banner_slika': upload((1800, 900)),
            'bestsellers_banner_link': 'https://example.com/stolice/',
        })
        self.assertEqual(response.status_code, 302)
        self.site.refresh_from_db()
        self.assertEqual(self.site.hero_slika.name, original_name)
        self.assertLessEqual(self.site.bestsellers_banner_slika.size, 80_000)
        with Image.open(self.site.bestsellers_banner_slika) as image:
            self.assertEqual(image.size, (1600, 600))
        html = render_to_string('partials/home_bestsellers_banner.html', {'site_settings': self.site})
        self.assertIn('https://example.com/stolice/', html)
        self.assertIn('loading="lazy"', html)
        self.assertNotIn('ribolovačkih stolica', html)
        response = self.client.post(reverse('staff_hero_settings'), {
            'hero_link': self.site.hero_link, 'bestsellers_banner_slika-clear': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.site.refresh_from_db()
        self.assertFalse(self.site.bestsellers_banner_slika)
        self.assertEqual(self.site.hero_slika.name, original_name)
        self.assertEqual(render_to_string('partials/home_bestsellers_banner.html', {'site_settings': self.site}).strip(), '')

    def test_bestsellers_banner_direct_model_upload_is_optimized(self):
        self.site.bestsellers_banner_slika = upload()
        self.site.save(update_fields=['bestsellers_banner_slika'])
        self.assertLessEqual(self.site.bestsellers_banner_slika.size, 80_000)
        with Image.open(self.site.bestsellers_banner_slika) as image:
            self.assertEqual(image.size, (1600, 600))
        response = self.client.get(reverse('staff_hero_settings'))
        self.assertContains(response, 'Banner ispod najprodavanijih proizvoda')
        self.assertContains(response, 'name="bestsellers_banner_slika"')
        self.assertContains(response, 'name="bestsellers_banner_link"')

    def test_banner_is_immediately_after_bestsellers_without_old_chair_block(self):
        from .models import Product
        product = Product.objects.create(naziv='Test artikal', cijena='25.00')
        self.site.bestsellers_banner_slika = upload()
        self.site.save(update_fields=['bestsellers_banner_slika'])
        html = render_to_string('home.html', {'site_settings': self.site, 'bestselling_products': [product]})
        start = html.index('id="najprodavaniji"')
        end = html.index('</section>', start) + len('</section>')
        self.assertTrue(html[end:].lstrip().startswith('<section class="home-uploaded-banner home-uploaded-banner--bestsellers"'))
        self.assertNotIn('Veliki izbor', html)
        self.assertNotIn('chair-banner', html)
