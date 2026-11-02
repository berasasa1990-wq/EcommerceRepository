from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .forms import HomepageProductImageForm
from .models import Product


class HomepageImageIsolationTests(SimpleTestCase):
    def test_only_one_homepage_upload_field(self):
        form = HomepageProductImageForm(instance=Product())
        self.assertEqual(set(form.fields), {'slika_pocetna'})

    def test_other_product_keeps_its_own_image(self):
        product = Product(naziv='Other', slika='products/other.png')
        html = render_to_string('partials/product_image.html', {'product': product, 'use_home_image': True})
        self.assertIn('/media/products/other.png', html)
        self.assertNotIn('products/homepage/', html)

    def test_homepage_image_works_without_main_image(self):
        product = Product(pk=1, naziv='Home', slika_pocetna='products/homepage/only.png')
        product._prefetched_objects_cache = {'varijacije': []}
        home = render_to_string('partials/product_image.html', {'product': product, 'use_home_image': True})
        detail = render_to_string('partials/product_image.html', {'product': product})
        self.assertIn('/media/products/homepage/only.png', home)
        self.assertNotIn('/media/products/homepage/only.png', detail)
        self.assertIn('product-image-placeholder', detail)
