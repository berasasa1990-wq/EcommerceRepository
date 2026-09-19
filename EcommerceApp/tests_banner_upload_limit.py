from io import BytesIO
from unittest.mock import patch

from PIL import Image, ImageDraw
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.core.files.storage import InMemoryStorage

from .utils.images import _limit_banner_file, process_banner_image_for_admin, save_responsive_variants


class BannerUploadLimitTests(SimpleTestCase):
    def test_admin_rejects_oversized_mobile_webp_as_field_error(self):
        from django import forms
        from .forms import BannerAdminForm
        mobile = forms.ImageField().clean(SimpleUploadedFile('mobile.webp', self.mobile_webp(200001)))
        form = BannerAdminForm()
        form.cleaned_data = {'tip': 'hero', 'slika_mobilna': mobile, 'slika': mobile}
        with self.assertRaises(forms.ValidationError) as error:
            form.clean()
        self.assertIn('slika_mobilna', error.exception.message_dict)

    def mobile_webp(self, size):
        buffer = BytesIO()
        Image.new('RGB', (1900, 400), '#5080a0').save(buffer, format='WEBP', lossless=True)
        return buffer.getvalue().ljust(size, b'\0')

    def test_mobile_webp_preserves_exact_bytes_and_dimensions_up_to_limit(self):
        for size in (10000, 200000):
            with self.subTest(size=size):
                original = self.mobile_webp(size)
                result = process_banner_image_for_admin(
                    SimpleUploadedFile('mobile.webp', original), tip='hero_mobile',
                )
                self.assertEqual(result.read(), original)
                result.seek(0)
                with Image.open(result) as image:
                    self.assertEqual(image.format, 'WEBP')
                    self.assertEqual(image.size, (1900, 400))

    def test_mobile_webp_over_limit_is_rejected_instead_of_recompressed(self):
        with self.assertRaisesMessage(ValueError, 'Mobilni WebP banner prelazi 200 KB'):
            process_banner_image_for_admin(
                SimpleUploadedFile('mobile.webp', self.mobile_webp(200001)), tip='hero_mobile',
            )

    def test_png_banner_keeps_full_resolution_and_pixels_when_lossless_webp_fits(self):
        image = Image.new('RGBA', (1900, 400), (20, 90, 150, 255))
        ImageDraw.Draw(image).text((40, 40), 'Banner - sitni tekst 1900 x 400', fill='white')
        buffer = BytesIO()
        image.save(buffer, format='PNG', compress_level=0)
        self.assertGreater(buffer.tell(), 200000)
        result = _limit_banner_file(ContentFile(buffer.getvalue(), name='banner.png'))
        self.assertLessEqual(result.size, 200000)
        self.assertTrue(result.name.endswith('.webp'))
        with Image.open(result) as decoded:
            self.assertEqual(decoded.size, image.size)
            self.assertEqual(decoded.convert('RGBA').tobytes(), image.tobytes())

    def test_webp_main_keeps_jpeg_variant_extension(self):
        buffer = BytesIO()
        Image.new('RGB', (640, 135)).save(buffer, format='JPEG')
        storage = InMemoryStorage()
        save_responsive_variants(storage, 'banner.webp', {
            640: ContentFile(buffer.getvalue(), name='banner-640w.png'),
        })
        self.assertTrue(storage.exists('banner-640w.jpg'))
        self.assertFalse(storage.exists('banner-640w.webp'))

    def noisy(self):
        buffer = BytesIO()
        Image.effect_noise((1800, 1000), 100).convert('RGB').save(buffer, format='PNG')
        return buffer.getvalue()

    def test_large_image_is_reencoded_under_200kb(self):
        original = self.noisy()
        self.assertGreater(len(original), 200000)
        output = _limit_banner_file(ContentFile(original, name='banner.png'))
        self.assertLessEqual(output.size, 200000)
        with Image.open(output) as image:
            image.verify()

    def test_small_file_is_not_recompressed(self):
        buffer = BytesIO()
        Image.new('RGB', (100, 50)).save(buffer, format='JPEG')
        original = buffer.getvalue()
        self.assertEqual(_limit_banner_file(ContentFile(original, name='small.jpg')).read(), original)

    def test_exact_limit_preserves_original_bytes_and_resolution(self):
        buffer = BytesIO()
        Image.new('RGB', (1920, 640), '#5080a0').save(buffer, format='JPEG', quality=95)
        original = buffer.getvalue().ljust(200000, b'\0')
        output = _limit_banner_file(ContentFile(original, name='banner.jpg'))
        self.assertEqual(output.read(), original)
        output.seek(0)
        with Image.open(output) as image:
            self.assertEqual(image.size, (1920, 640))

    def test_file_between_old_and_new_limit_is_reencoded_without_resizing(self):
        buffer = BytesIO()
        Image.new('RGB', (1920, 640), '#5080a0').save(buffer, format='JPEG', quality=95)
        original = buffer.getvalue().ljust(250000, b'\0')
        output = _limit_banner_file(ContentFile(original, name='banner.jpg'))
        self.assertLessEqual(output.size, 200000)
        with Image.open(output) as image:
            self.assertEqual(image.size, (1920, 640))
            self.assertEqual(image.format, 'JPEG')

    def test_every_banner_type_caps_main_and_responsive_fallbacks(self):
        data = self.noisy()
        for tip in ('hero', 'hero_mobile', 'grid', 'featured', 'spotlight'):
            with self.subTest(tip=tip):
                result = {'main': ContentFile(data, name='banner.png'),
                          'variants': {640: ContentFile(data, name='banner-640w.png')}}
                with patch('EcommerceApp.utils.images._process_banner_image_for_admin', return_value=result):
                    processed = process_banner_image_for_admin(None, tip=tip)
                self.assertLessEqual(processed['main'].size, 200000)
                self.assertLessEqual(processed['variants'][640].size, 200000)

    def test_real_hero_and_mobile_uploads_fit_limit(self):
        data = self.noisy()
        for tip in ('hero', 'hero_mobile'):
            result = process_banner_image_for_admin(SimpleUploadedFile('banner.png', data), tip=tip)
            files = [result['main'], *result['variants'].values()] if isinstance(result, dict) else [result]
            self.assertTrue(all(file.size <= 200000 for file in files))
