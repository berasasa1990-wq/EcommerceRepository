from django.test import TestCase
from .models import Category, Product
from .views import _related_category_products


class RelatedProductStockTests(TestCase):
    def test_in_stock_precedes_priority_and_limit(self):
        category = Category.objects.create(naziv='Povezani test')
        current = Product.objects.create(naziv='Glavni', kategorija=category, cijena=10)
        unavailable = Product.objects.create(
            naziv='Prioritetni rasprodati', kategorija=category, cijena=10,
            na_stanju=False, stanje=0, prioritet_lagera=2,
        )
        available = Product.objects.create(
            naziv='Dostupni', kategorija=category, cijena=10,
            na_stanju=True, stanje=5, prioritet_lagera=0,
        )
        self.assertEqual(
            [item.pk for item in _related_category_products(current, limit=2)],
            [available.pk, unavailable.pk],
        )
        self.assertEqual(_related_category_products(current, limit=1)[0].pk, available.pk)
