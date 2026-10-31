from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import SimpleTestCase
from PIL import Image, ImageDraw

from .utils.images import prepare_promo_banner


class PromoImageLimitTests(SimpleTestCase):
    def upload(self, image, **options):
        buffer = BytesIO()
        image.save(buffer, format='PNG', **options)
        return ContentFile(buffer.getvalue(), name='banner.png')

    def test_small_image_is_kept_byte_for_byte(self):
        original = self.upload(Image.new('RGB', (600, 560), '#123456'))
        expected = original.read()
        result = prepare_promo_banner(original, mobile=True)
        self.assertEqual(result.read(), expected)

    def test_lossless_compression_preserves_text_and_pixels(self):
        image = Image.new('RGBA', (1000, 500), '#263746')
        ImageDraw.Draw(image).text((30, 30), 'Zimska garderoba - sitni tekst 123456789', fill='white')
        original = self.upload(image, compress_level=0)
        self.assertGreater(original.size, 60000)
        result = prepare_promo_banner(original)
        self.assertLessEqual(result.size, 60000)
        with Image.open(result) as decoded:
            self.assertEqual(decoded.size, image.size)
            self.assertEqual(decoded.convert('RGBA').tobytes(), image.tobytes())

    def test_detailed_mobile_banner_is_valid_and_under_60kb(self):
        image = Image.effect_noise((600, 560), 70).convert('RGB')
        original = self.upload(image)
        result = prepare_promo_banner(original, mobile=True)
        self.assertLessEqual(result.size, 60000)
        with Image.open(result) as decoded:
            self.assertIn(decoded.format, ('WEBP', 'AVIF'))
            self.assertAlmostEqual(decoded.width / decoded.height, 600 / 560, places=2)
            self.assertGreaterEqual(decoded.width, 320)

    def test_optimised_result_is_not_lossily_encoded_again(self):
        image = Image.effect_noise((600, 560), 70).convert('RGB')
        first = prepare_promo_banner(self.upload(image), mobile=True)
        expected = first.read()
        result = prepare_promo_banner(first, mobile=True)
        self.assertEqual(result.read(), expected)

    def test_invalid_upload_is_rejected(self):
        with self.assertRaises(ValidationError):
            prepare_promo_banner(ContentFile(b'not an image', name='bad.png'))
