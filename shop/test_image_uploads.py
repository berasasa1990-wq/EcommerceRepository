from io import BytesIO
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from .forms import BrandForm, LogoForm
from .image_uploads import MAX_IMAGE_BYTES, optimize_image
from .models import Banner, Brand, Category, Product, StoreSettings


def upload(image=None, format='PNG'):
    image = image if image is not None else Image.new('RGBA', (300, 150), (20, 80, 160, 120))
    buffer = BytesIO()
    image.save(buffer, format=format)
    return SimpleUploadedFile('example.' + format.lower(), buffer.getvalue(), content_type='image/' + format.lower())


class ImageUploadTests(TestCase):
    def assert_avif(self, file):
        self.assertTrue(file.name.endswith('.avif'))
        self.assertLessEqual(file.size, MAX_IMAGE_BYTES)
        file.seek(0)
        with Image.open(file) as image:
            self.assertEqual(image.format, 'AVIF')
            image.load()

    def test_transparency_and_proportions(self):
        result = optimize_image(upload())
        self.assert_avif(result)
        result.seek(0)
        with Image.open(result) as image:
            self.assertEqual(image.size, (300, 150))
            self.assertEqual(image.mode, 'RGBA')
            self.assertLess(image.getpixel((0, 0))[3], 255)

    def test_noisy_image_obeys_hard_size_limit(self):
        image = Image.effect_noise((1800, 1200), 100).convert('RGB')
        self.assert_avif(optimize_image(upload(image)))

    def test_all_model_uploads_and_unrelated_edits(self):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            items = [
                (Brand(name='Test'), 'logo'),
                (Category(name='Test'), 'image'),
                (Product(name='Test', price='10'), 'image'),
                (Banner(title='Test', position='offer_left'), 'image'),
                (StoreSettings(), 'logo'),
            ]
            for instance, field in items:
                with self.subTest(model=type(instance).__name__):
                    setattr(instance, field, upload())
                    instance.save()
                    file = getattr(instance, field)
                    self.assert_avif(file)
                    name = file.name
                    file.close()
                    instance.save()
                    self.assertEqual(getattr(instance, field).name, name)

    def test_forms_convert_uploads_and_accept_avif(self):
        for form in (BrandForm({'name': 'Test'}, {'logo': upload()}), LogoForm({}, {'logo': upload()})):
            self.assertTrue(form.is_valid(), form.errors)
            self.assert_avif(form.cleaned_data['logo'])
        avif = optimize_image(upload())
        form = LogoForm({}, {'logo': SimpleUploadedFile('logo.avif', avif.read(), 'image/avif')})
        self.assertTrue(form.is_valid(), form.errors)
        self.assert_avif(form.cleaned_data['logo'])

    def test_invalid_file_is_a_form_error(self):
        form = BrandForm({'name': 'Test'}, {'logo': SimpleUploadedFile('fake.png', b'not an image')})
        self.assertFalse(form.is_valid())
        self.assertIn('logo', form.errors)
        with self.assertRaises(ValidationError):
            optimize_image(SimpleUploadedFile('fake.avif', b'not an image'))
