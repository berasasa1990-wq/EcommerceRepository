from io import BytesIO

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Product, ProductImage
from .product_social_images import share_image_context


@override_settings(SECURE_SSL_REDIRECT=False, SITE_PREP_ENABLED=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ProductSocialImageTests(TestCase):
    def setUp(self):
        cache.clear()

    def stored_image(self, name, color):
        output = BytesIO()
        Image.new('RGB', (600, 600), color).save(output, 'PNG')
        return default_storage.save(name, ContentFile(output.getvalue()))

    def product(self, **kwargs):
        image = self.stored_image('products/social-main.png', 'red')
        return Product.objects.create(naziv='Artikal za dijeljenje', cijena=10, slika=image, **kwargs)

    def test_anonymous_preview_is_jpeg_and_contains_the_product_photo(self):
        product = self.product()
        response = self.client.get(reverse('product_share_image', kwargs={'slug': product.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        head = self.client.head(reverse('product_share_image', kwargs={'slug': product.slug}))
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head['Content-Type'], 'image/jpeg')
        with Image.open(BytesIO(response.content)) as image:
            self.assertEqual(image.size, (1200, 630))
            red, green, blue = image.getpixel((270, 350))
            self.assertGreater(red, 240)
            self.assertLess(green, 15)
            self.assertLess(blue, 15)

    def test_product_page_has_its_own_og_and_twitter_image(self):
        product = self.product()
        response = self.client.get(product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        url = share_image_context(RequestFactory().get('/'), product)['og_image']
        self.assertContains(response, f'property="og:image" content="{url}"')
        self.assertContains(response, f'name="twitter:image" content="{url}"')
        self.assertContains(response, 'property="og:image:type" content="image/jpeg"')
        self.assertContains(response, 'property="og:image:width" content="1200"')

    def test_missing_main_file_falls_back_to_gallery(self):
        product = Product.objects.create(naziv='Nedostaje glavna', cijena=10, slika='products/missing.avif')
        extra = self.stored_image('products/social-extra.png', 'blue')
        ProductImage.objects.create(product=product, slika=extra)
        response = self.client.get(reverse('product_share_image', kwargs={'slug': product.slug}))
        self.assertEqual(response.status_code, 200)
        with Image.open(BytesIO(response.content)) as image:
            red, green, blue = image.getpixel((270, 350))
            self.assertGreater(blue, 240)
            self.assertLess(red, 15)

    def test_replacing_main_image_changes_the_preview_url(self):
        product = self.product()
        request = RequestFactory().get('/')
        before = share_image_context(request, product)['og_image']
        product.slika = self.stored_image('products/social-new.png', 'blue')
        after = share_image_context(request, product)['og_image']
        self.assertNotEqual(before, after)

    def test_hidden_and_inactive_products_do_not_expose_share_images(self):
        for field in ('aktivan', 'sakriven_do_stanja'):
            product = self.product()
            Product.objects.filter(pk=product.pk).update(**{field: field != 'aktivan'})
            response = self.client.get(reverse('product_share_image', kwargs={'slug': product.slug}))
            self.assertEqual(response.status_code, 404)
            product.delete()

    def test_price_and_name_changes_refresh_the_shared_card(self):
        product = self.product()
        request = RequestFactory().get('/')
        original = share_image_context(request, product)['og_image']
        product.cijena = 25
        self.assertNotEqual(original, share_image_context(request, product)['og_image'])
        product.cijena = 10
        product.naziv = 'Novi naziv sa slovima ŠČĆŽĐ'
        self.assertNotEqual(original, share_image_context(request, product)['og_image'])
