import io
import tempfile

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

from .forms import FeaturedProductImageForm
from .models import Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class NewArrivalsImageTests(TestCase):
    def test_upload_clear_and_section_isolation(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            product = Product.objects.create(naziv='New image test', cijena=10,
                slika='products/main.png', slika_izdvojena='products/featured/existing.png')
            data = io.BytesIO()
            Image.new('RGB', (40, 40), 'green').save(data, 'PNG')
            upload = SimpleUploadedFile('new.png', data.getvalue(), content_type='image/png')
            form = FeaturedProductImageForm({}, {'slika_novo': upload}, instance=product)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            product.refresh_from_db()
            self.assertTrue(product.slika_novo.name.startswith('products/new/'))
            self.assertEqual(product.slika.name, 'products/main.png')
            self.assertEqual(product.slika_izdvojena.name, 'products/featured/existing.png')
            new_image_url = product.slika_novo.url
            new = render_to_string('partials/product_image.html', {'product': product, 'use_new_image': True})
            normal = render_to_string('partials/product_image.html', {'product': product})
            featured = render_to_string('partials/product_image.html', {'product': product, 'use_featured_image': True})
            self.assertIn(new_image_url, new)
            self.assertIn('media="(min-width: 768px)"', new)
            self.assertNotIn(new_image_url, normal)
            self.assertNotIn(new_image_url, featured)
            clear = FeaturedProductImageForm({'slika_novo-clear': 'on'}, instance=product)
            self.assertTrue(clear.is_valid(), clear.errors)
            clear.save()
            product.refresh_from_db()
            self.assertFalse(product.slika_novo)
            fallback = render_to_string('partials/product_image.html', {'product': product, 'use_new_image': True})
            self.assertIn('/media/products/main.png', fallback)

    def test_invalid_new_image_is_rejected(self):
        product = Product(naziv='Invalid upload', cijena=10)
        form = FeaturedProductImageForm({}, {'slika_novo': SimpleUploadedFile('bad.png', b'bad')}, instance=product)
        self.assertFalse(form.is_valid())
        self.assertIn('slika_novo', form.errors)
