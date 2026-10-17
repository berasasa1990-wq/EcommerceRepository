from django.test import TestCase, RequestFactory
from .models import Product, Uvoz, UvozStavka
from .views import _home_latest_products, _apply_product_filters


class NovitetiSelectionTests(TestCase):
    def setUp(self):
        self.old = Product.objects.create(naziv='Stari', cijena=10, stanje=3, na_stanju=True)
        self.new = Product.objects.create(naziv='Zadnji uvoz', cijena=10, stanje=3, na_stanju=True)
        old_import = Uvoz.objects.create(naziv='Prvi')
        self.latest = Uvoz.objects.create(naziv='Zadnji')
        UvozStavka.objects.create(uvoz=old_import, product=self.old, status='created')
        UvozStavka.objects.create(uvoz=self.latest, product=self.new, status='updated')
        UvozStavka.objects.create(uvoz=self.latest, product=self.new, status='updated')

    def test_latest_import_is_stable_and_deduplicated(self):
        for _ in range(2):
            self.assertEqual([p.pk for p in _home_latest_products()], [self.new.pk])

    def test_out_of_stock_does_not_fall_back_to_older_import(self):
        self.new.stanje = 0
        self.new.na_stanju = False
        self.new.save()
        self.assertEqual(_home_latest_products(), [])

    def test_unfinished_import_is_ignored(self):
        draft = Uvoz.objects.create(naziv='Nedovrsen')
        UvozStavka.objects.create(uvoz=draft, product=self.old, status='pending')
        self.assertEqual([p.pk for p in _home_latest_products()], [self.new.pk])

    def test_see_all_uses_latest_import(self):
        request = RequestFactory().get('/', {'noviteti': '1'})
        products, _ = _apply_product_filters(Product.objects.filter(aktivan=True), request)
        self.assertEqual([p.pk for p in products], [self.new.pk])
