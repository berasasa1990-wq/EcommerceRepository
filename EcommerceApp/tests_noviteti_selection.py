from unittest.mock import patch
from django.test import TestCase, RequestFactory
from .models import Product
from .views import _home_latest_products, _apply_product_filters


class NovitetiSelectionTests(TestCase):
    def test_random_ten_from_recent_available_products(self):
        products = [Product.objects.create(naziv=f'Novo {i}', cijena=10, stanje=2, na_stanju=True) for i in range(52)]
        Product.objects.create(naziv='Nema', cijena=10, stanje=0, na_stanju=False, je_novitet=True)
        with patch('EcommerceApp.views.random.sample', side_effect=lambda ids, count: ids[:count]) as sample:
            selected = _home_latest_products()
        self.assertEqual(len(selected), 10)
        self.assertEqual(len(set(p.pk for p in selected)), 10)
        self.assertEqual(len(sample.call_args.args[0]), 50)
        self.assertNotIn(products[0].pk, sample.call_args.args[0])
        self.assertTrue(all(p.stanje > 0 for p in selected))

    def test_see_all_uses_positive_stock_instead_of_manual_flag(self):
        available = Product.objects.create(naziv='Dostupan', cijena=10, stanje=3, na_stanju=True, je_novitet=False)
        Product.objects.create(naziv='Rasprodan', cijena=10, stanje=0, na_stanju=True, je_novitet=True)
        request = RequestFactory().get('/', {'noviteti': '1'})
        products, _ = _apply_product_filters(Product.objects.filter(aktivan=True), request)
        self.assertEqual([p.pk for p in products], [available.pk])
