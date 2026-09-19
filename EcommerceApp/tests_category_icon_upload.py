from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Category
from .utils.images import process_category_icon


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}})
class CategoryIconUploadTests(TestCase):
    def upload(self, noisy=False):
        image = Image.effect_noise((1000, 800), 100).convert('RGBA') if noisy else Image.new('RGBA', (400, 300), (255, 0, 0, 0))
        output = BytesIO()
        image.save(output, format='PNG')
        return SimpleUploadedFile('icon.png', output.getvalue(), content_type='image/png')

    def test_new_upload_is_avif_under_limit_and_keeps_transparency(self):
        category = Category.objects.create(naziv='Icon test', ikonica_pocetna=self.upload())
        self.assertTrue(category.ikonica_pocetna.name.endswith('.avif'))
        self.assertLessEqual(category.ikonica_pocetna.size, 15000)
        with Image.open(category.ikonica_pocetna) as image:
            self.assertEqual(image.format, 'AVIF')
            self.assertLessEqual(max(image.size), 256)
            self.assertEqual(image.convert('RGBA').getpixel((0, 0))[3], 0)
        category.refresh_from_db()
        with patch('EcommerceApp.utils.images.process_category_icon') as converter:
            category.naziv = 'Renamed'
            category.save()
            converter.assert_not_called()

    def test_complex_upload_respects_hard_limit(self):
        result = process_category_icon(self.upload(noisy=True))
        self.assertLessEqual(result.size, 15000)
        with Image.open(result) as image:
            self.assertEqual(image.format, 'AVIF')

    def test_encoder_failure_does_not_replace_existing_icon(self):
        category = Category.objects.create(naziv='Keep icon', ikonica_pocetna=self.upload())
        original = category.ikonica_pocetna.name
        category.ikonica_pocetna = self.upload(noisy=True)
        with patch('EcommerceApp.utils.images._encode_avif', side_effect=OSError('encoder unavailable')):
            with self.assertRaises(OSError):
                category.save()
        category.refresh_from_db()
        self.assertEqual(category.ikonica_pocetna.name, original)

    def test_reupload_changes_url_and_refreshes_cached_home_categories(self):
        from django.core.cache import cache
        from .models import Product
        from .context_processors import _build_nav_categories
        cache.clear()
        self.addCleanup(cache.clear)
        with self.captureOnCommitCallbacks(execute=True):
            category = Category.objects.create(naziv='Fresh icon', ikonica_pocetna=self.upload())
        Product.objects.create(naziv='Visible product', cijena=10, kategorija=category)
        original_url = _build_nav_categories()[0].ikonica_pocetna.url
        category.ikonica_pocetna = self.upload(noisy=True)
        with self.captureOnCommitCallbacks(execute=True):
            category.save()
        fresh = _build_nav_categories()[0]
        self.assertNotEqual(fresh.ikonica_pocetna.url, original_url)
        self.assertEqual(fresh.ikonica_pocetna.url, category.ikonica_pocetna.url)
        self.assertTrue(fresh.ikonica_pocetna.name.endswith('.avif'))
        self.assertLessEqual(fresh.ikonica_pocetna.size, 15000)
