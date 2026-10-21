from django.test import RequestFactory, TestCase
from .models import Product, Category, Brand
from .views import _apply_product_filters


class CatalogNewestTests(TestCase):
    def test_category_and_brand_default_to_latest_even_when_more_expensive(self):
        category = Category.objects.create(naziv='Test kategorija', slug='test-kategorija')
        brand = Brand.objects.create(naziv='Test brend', slug='test-brend')
        old = Product.objects.create(naziv='Stari', cijena=5, kategorija=category, brend=brand, na_stanju=True, stanje=3)
        new = Product.objects.create(naziv='Novi', cijena=50, kategorija=category, brend=brand, na_stanju=True, stanje=3)
        for params, kwargs in (({}, {'allowed_category_ids': [category.pk]}), ({'brend': brand.slug}, {})):
            request = RequestFactory().get('/', params)
            products, _ = _apply_product_filters(Product.objects.all(), request, **kwargs)
            self.assertEqual([p.pk for p in products], [new.pk, old.pk])
            request = RequestFactory().get('/', {**params, 'sort': 'rastuca'})
            products, _ = _apply_product_filters(Product.objects.all(), request, **kwargs)
            self.assertEqual([p.pk for p in products], [old.pk, new.pk])
