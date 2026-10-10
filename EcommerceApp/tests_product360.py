import io
import tempfile
from zipfile import ZipFile
from PIL import Image
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.urls import reverse
from .models import Product
from .product360 import prepare_frames, replace_frames


def photo(name, color=0, size=(32, 32)):
    output = io.BytesIO()
    Image.new('RGB', size, (color, 0, 0)).save(output, 'PNG')
    return SimpleUploadedFile(name, output.getvalue(), content_type='image/png')


class Product360Tests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.directory.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.product = Product.objects.create(naziv='360 test', cijena=10, enabled_360=True)

    def test_zip_24_frames_natural_order_and_webp(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            for n in reversed(range(1, 25)):
                archive.writestr(f'{n}.png', photo(f'{n}.png', n * 8).read())
        frames = prepare_frames(SimpleUploadedFile('frames.zip', stream.getvalue()))
        self.assertEqual(len(frames), 24)
        red = [Image.open(io.BytesIO(frame)).getpixel((0, 0))[0] for frame in frames]
        self.assertEqual(red, sorted(red))
        replace_frames(self.product, frames)
        self.assertEqual(self.product.images_360.count(), 24)
        self.assertTrue(all(row.image.name.endswith('.webp') for row in self.product.images_360.all()))

    def test_invalid_upload_keeps_existing_set(self):
        replace_frames(self.product, prepare_frames(uploads=[photo('1.png'), photo('2.png')]))
        old = list(self.product.images_360.values_list('image', flat=True))
        for uploads in ([photo('1.png')], [photo('1.png'), photo('2.png', size=(64, 32))], [photo('1.png'), SimpleUploadedFile('2.png', b'bad')]):
            with self.assertRaises(ValidationError):
                prepare_frames(uploads=uploads)
        self.assertEqual(old, list(self.product.images_360.values_list('image', flat=True)))

    def test_traversal_and_non_image_zip_rejected(self):
        for name in ('../1.png', 'script.html'):
            stream = io.BytesIO()
            with ZipFile(stream, 'w') as archive:
                archive.writestr(name, b'bad')
            with self.assertRaises(ValidationError):
                prepare_frames(SimpleUploadedFile('bad.zip', stream.getvalue()))

    def test_replace_delete_preserves_normal_gallery(self):
        from .models import ProductImage
        normal = ProductImage.objects.create(product=self.product, slika=photo('normal.png'))
        self.product.slika = photo('main.png')
        self.product.save()
        main = self.product.slika.name
        replace_frames(self.product, prepare_frames(uploads=[photo('1.png'), photo('2.png')]))
        old = list(self.product.images_360.values_list('image', flat=True))
        with self.captureOnCommitCallbacks(execute=True):
            replace_frames(self.product, prepare_frames(uploads=[photo('3.png'), photo('4.png'), photo('5.png')]))
        self.assertEqual(self.product.images_360.count(), 3)
        self.assertNotEqual(old, list(self.product.images_360.values_list('image', flat=True)))
        replace_frames(self.product, delete=True)
        self.assertEqual(self.product.images_360.count(), 0)
        normal.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.product.slika.name, main)
        self.assertTrue(normal.slika.storage.exists(normal.slika.name))

    def test_gallery_visibility_enabled_and_disabled(self):
        url = reverse('product_detail', args=[self.product.slug])
        self.assertNotContains(self.client.get(url), 'id="product360Open"')
        replace_frames(self.product, prepare_frames(uploads=[photo('1.png'), photo('2.png')]))
        self.assertContains(self.client.get(url), 'id="product360Open"')
        self.product.enabled_360 = False
        self.product.save()
        self.assertNotContains(self.client.get(url), 'id="product360Open"')

    def test_anonymous_cannot_upload_to_editor(self):
        response = self.client.post(f'/panel/podesavanja/sekcije/EcommerceApp/product/{self.product.pk}/change/', {'upload_360_images': [photo('1.png'), photo('2.png')]})
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(self.product.images_360.exists())

    def test_editor_form_upload_and_invalid_replacement(self):
        from .admin_forms import ProductTagsAdminForm
        class UploadForm(ProductTagsAdminForm):
            class Meta(ProductTagsAdminForm.Meta):
                fields = ('enabled_360',)
        form = UploadForm(data={'enabled_360': 'on'}, files={'upload_360_zip': self._zip()}, instance=self.product)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(self.product.images_360.count(), 24)
        old = list(self.product.images_360.values_list('image', flat=True))
        invalid = UploadForm(data={'enabled_360': 'on'}, files={'upload_360_zip': SimpleUploadedFile('bad.zip', b'invalid')}, instance=self.product)
        self.assertFalse(invalid.is_valid())
        self.assertIn('upload_360_zip', invalid.errors)
        self.assertEqual(old, list(self.product.images_360.values_list('image', flat=True)))
        delete = UploadForm(data={'delete_360': 'on'}, instance=self.product)
        self.assertTrue(delete.is_valid(), delete.errors)
        delete.save()
        self.assertFalse(self.product.images_360.exists())

    def _zip(self):
        stream = io.BytesIO()
        with ZipFile(stream, 'w') as archive:
            for n in reversed(range(1, 25)):
                archive.writestr(f'{n:03}.png', photo(f'{n}.png', n * 8).read())
        return SimpleUploadedFile('frames.zip', stream.getvalue())

    def test_existing_panel_editor_has_upload_fields_and_csrf(self):
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_superuser('spin-editor', 'spin@example.invalid', 'test-pass')
        self.client.force_login(user)
        url = f'/panel/podesavanja/sekcije/EcommerceApp/product/{self.product.pk}/change/'
        response = self.client.get(url)
        self.assertContains(response, 'name="upload_360_zip"')
        self.assertContains(response, 'name="upload_360_images"')
        self.assertContains(response, 'csrfmiddlewaretoken')
        from django.test import Client
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(user)
        self.assertEqual(protected.post(url, {'delete_360': 'on'}).status_code, 403)

    def test_storage_failure_does_not_replace_existing_set(self):
        from unittest.mock import patch
        from .models import Product360Image
        frames = prepare_frames(uploads=[photo('1.png'), photo('2.png')])
        replace_frames(self.product, frames)
        old = list(self.product.images_360.values_list('image', flat=True))
        storage = Product360Image._meta.get_field('image').storage
        with patch.object(storage, 'save', side_effect=OSError('storage unavailable')):
            with self.assertRaises(OSError):
                replace_frames(self.product, frames)
        self.assertEqual(old, list(self.product.images_360.values_list('image', flat=True)))
        self.assertTrue(all(storage.exists(name) for name in old))
