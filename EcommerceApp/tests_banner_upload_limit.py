from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from .utils.images import _limit_banner_file, process_banner_image_for_admin


class BannerUploadLimitTests(SimpleTestCase):
    def noisy(self):
        buffer = BytesIO()
        Image.effect_noise((1800, 1000), 100).convert('RGB').save(buffer, format='PNG')
        return buffer.getvalue()

    def test_large_image_is_reencoded_under_300kb(self):
        original = self.noisy()
        self.assertGreater(len(original), 300000)
        output = _limit_banner_file(ContentFile(original, name='banner.png'))
        self.assertLessEqual(output.size, 300000)
        with Image.open(output) as image:
            image.verify()

    def test_small_file_is_not_recompressed(self):
        buffer = BytesIO()
        Image.new('RGB', (100, 50)).save(buffer, format='JPEG')
        original = buffer.getvalue()
        self.assertEqual(_limit_banner_file(ContentFile(original, name='small.jpg')).read(), original)

    def test_every_banner_type_caps_main_and_responsive_fallbacks(self):
        data = self.noisy()
        for tip in ('hero', 'hero_mobile', 'grid', 'featured', 'spotlight'):
            with self.subTest(tip=tip):
                result = {'main': ContentFile(data, name='banner.png'),
                          'variants': {640: ContentFile(data, name='banner-640w.png')}}
                with patch('EcommerceApp.utils.images._process_banner_image_for_admin', return_value=result):
                    processed = process_banner_image_for_admin(None, tip=tip)
                self.assertLessEqual(processed['main'].size, 300000)
                self.assertLessEqual(processed['variants'][640].size, 300000)

    def test_real_hero_and_mobile_uploads_fit_limit(self):
        data = self.noisy()
        for tip in ('hero', 'hero_mobile'):
            result = process_banner_image_for_admin(SimpleUploadedFile('banner.png', data), tip=tip)
            files = [result['main'], *result['variants'].values()] if isinstance(result, dict) else [result]
            self.assertTrue(all(file.size <= 300000 for file in files))
