import io
import re
import tempfile
from unittest.mock import Mock, patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import HomepageProductImageForm
from .models import HomeFeaturedProduct, Product, SiteSettings


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class HomepageProductImageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('featured-image-admin', 'fixture@example.com', 'password')
        cls.product = Product.objects.create(naziv='Test featured image', cijena=10,
            slika='products/main.png', na_stanju=True, stanje=10, prikazi_na_pocetnoj=True,
            je_novitet=True, magacin_sync_at=timezone.now(), slika_pocetna='products/homepage/special.png')

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.directory.name)
        self.media.enable()
        self.addCleanup(self.media.disable)
        cache.clear()

    def upload(self, name='featured.png'):
        data = io.BytesIO()
        Image.new('RGB', (40, 40), '#ff9800').save(data, 'PNG')
        return SimpleUploadedFile(name, data.getvalue(), content_type='image/png')

    def test_override_only_when_explicitly_enabled_and_fallback(self):
        special = render_to_string('partials/product_image.html', {'product': self.product, 'use_home_image': True})
        normal = render_to_string('partials/product_image.html', {'product': self.product})
        self.assertIn('/media/products/homepage/special.png', special)
        self.assertIn('media="(min-width: 1025px)" srcset="/media/products/main.png"', special)
        self.assertIn('/media/products/main.png', normal)
        self.assertNotIn('/media/products/homepage/special.png', normal)
        self.product.slika_pocetna = ''
        self.assertIn('/media/products/main.png', render_to_string('partials/product_image.html',
            {'product': self.product, 'use_home_image': True}))

    def test_featured_can_display_special_image_without_main(self):
        self.product.slika = None
        html = render_to_string('partials/product_image.html', {'product': self.product, 'use_home_image': True})
        self.assertIn('/media/products/homepage/special.png', html)
        self.assertNotIn('product-image-placeholder', html)

    def test_form_validates_upload_keeps_main_and_supports_clear(self):
        form = HomepageProductImageForm({}, {'slika_pocetna': self.upload()}, instance=self.product)
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertTrue(product.slika_pocetna.name.startswith('products/homepage/'))
        self.assertTrue(product.slika_pocetna.storage.exists(product.slika_pocetna.name))
        self.assertEqual(product.slika.name, 'products/main.png')
        clear = HomepageProductImageForm({'slika_pocetna-clear': 'on'}, instance=product)
        self.assertTrue(clear.is_valid(), clear.errors)
        clear.save()
        product.refresh_from_db()
        self.assertFalse(product.slika_pocetna)
        self.assertEqual(product.slika.name, 'products/main.png')

    def test_invalid_upload_is_rejected(self):
        form = HomepageProductImageForm({}, {'slika_pocetna': SimpleUploadedFile('bad.png', b'not an image', content_type='image/png')}, instance=self.product)
        self.assertFalse(form.is_valid())
        self.assertIn('slika_pocetna', form.errors)

    def test_remote_featured_image_render_does_not_read_or_probe_storage(self):
        storage = type('CloudflareR2Storage', (), {})()
        storage.url = lambda name: 'https://media.opremazaribolov.ba/' + name
        storage.open = Mock(side_effect=AssertionError('Remote read during render'))
        storage.exists = Mock(side_effect=AssertionError('Remote probe during render'))
        self.product.slika_pocetna.storage = storage
        html = render_to_string('partials/product_image.html',
            {'product': self.product, 'use_home_image': True})
        self.assertIn('https://media.opremazaribolov.ba/products/homepage/special.png', html)
        storage.open.assert_not_called()
        storage.exists.assert_not_called()

    def test_unchanged_save_does_not_process_images(self):
        with patch('EcommerceApp.utils.images.process_product_image_named') as processor:
            self.product.save(update_fields=['naziv'])
            # Processor factories may be created; their returned callable must not run.
            processor.return_value.assert_not_called()

    def test_magacin_upload_remove_and_authorization(self):
        url = reverse('staff_magacin_artikal_izmjena', args=[self.product.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), 'name="slika_pocetna"')
        data = {'naziv': self.product.naziv, 'cijena': '10', 'aktivan': '1', 'slika_pocetna': self.upload()}
        with patch('EcommerceApp.views._invalidate_storefront_product_caches') as invalidate:
            response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        invalidate.assert_called_once()
        self.product.refresh_from_db()
        self.assertTrue(self.product.slika_pocetna.name.startswith('products/homepage/'))
        self.assertEqual(self.product.slika.name, 'products/main.png')
        response = self.client.post(url, {'naziv': self.product.naziv, 'cijena': '10', 'aktivan': '1', 'slika_pocetna-clear': 'on'})
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertFalse(self.product.slika_pocetna)

    def test_quick_activation_upload(self):
        from .models import Brand, Category
        brand = Brand.objects.create(naziv='Fixture brand')
        category = Category.objects.create(naziv='Fixture category')
        self.client.force_login(self.admin)
        url = reverse('staff_magacin_brzi_unos_aktivacija', args=[self.product.pk])
        self.assertContains(self.client.get(url), 'name="slika_pocetna"')
        response = self.client.post(url, {'action': 'activate', 'cijena': '10', 'brend_id': brand.pk,
            'kategorija_id': category.pk, 'keep_existing_image': '1',
            'slika_pocetna': self.upload()})
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertTrue(self.product.slika_pocetna.name.startswith('products/homepage/'))
        self.assertEqual(self.product.slika.name, 'products/main.png')

    def test_storefront_edit_upload_keeps_gallery_image(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session['staff_edit_mode'] = True
        session.save()
        url = reverse('staff_product_quick_edit', args=[self.product.slug])
        response = self.client.post(url, {'action': 'save_all', 'slika_pocetna': self.upload()})
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertTrue(self.product.slika_pocetna.name.startswith('products/homepage/'))
        self.assertEqual(self.product.slika.name, 'products/main.png')

    def test_homepage_override_applies_to_all_home_sections_but_not_detail(self):
        settings = SiteSettings.load()
        HomeFeaturedProduct.objects.create(postavke=settings, artikal=self.product)
        with patch('EcommerceApp.views._home_latest_products', return_value=[self.product]):
            html = self.client.get(reverse('home')).content.decode()
        featured = re.search(r'<section[^>]*home-products--izdvojeno[^>]*>(.*?)</section>', html, re.S).group(1)
        self.assertIn('/media/products/homepage/special.png', featured)
        # Mobile keeps the homepage override; desktop uses the main product image.
        latest = re.search(r'<section[^>]*home-products--novo[^>]*>(.*?)</section>', html, re.S).group(1)
        self.assertIn('/media/products/homepage/special.png', latest)
        self.assertIn('media="(min-width: 1025px)" srcset="/media/products/main.png"', latest)
        detail = self.client.get(self.product.get_absolute_url()).content.decode()
        self.assertIn('/media/products/main.png', detail)
        self.assertNotIn('/media/products/homepage/special.png', detail)
