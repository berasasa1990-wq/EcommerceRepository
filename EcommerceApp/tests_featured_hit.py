from unittest.mock import patch
from django.test import TestCase
from .models import Product
from .views import _featured_catalog_qs, _home_featured_products_uncached


class FeaturedHitTests(TestCase):
    def test_only_hit_products_in_home_and_catalog(self):
        hit = Product.objects.create(naziv='HIT', cijena=10, je_hit=True)
        Product.objects.create(naziv='Običan', cijena=10, je_hit=False)
        with patch('EcommerceApp.views._home_product_queryset', return_value=Product.objects.all()):
            self.assertEqual([p.pk for p in _home_featured_products_uncached()], [hit.pk])
            self.assertEqual(list(_featured_catalog_qs(Product.objects.all()).values_list('pk', flat=True)), [hit.pk])
            hit.je_hit = False
            hit.save(update_fields=['je_hit'])
            self.assertEqual(_home_featured_products_uncached(), [])
            self.assertFalse(_featured_catalog_qs(Product.objects.all()).exists())
