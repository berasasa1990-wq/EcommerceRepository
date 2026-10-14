from django.test import TestCase
from .models import Product, Uvoz, UvozStavka
from .views import _latest_warehouse_import_products


class LatestImportCatalogTests(TestCase):
    def test_only_latest_warehouse_import_including_out_of_stock(self):
        old = Product.objects.create(naziv='Stari', cijena=10, na_stanju=True, stanje=4)
        new = Product.objects.create(naziv='Novi', cijena=10, na_stanju=False, stanje=0)
        first = Uvoz.objects.create(naziv='Raniji', izvor=Uvoz.Izvor.MAGACIN)
        UvozStavka.objects.create(uvoz=first, product=old, artikal_naziv=old.naziv)
        latest = Uvoz.objects.create(naziv='Posljednji', izvor=Uvoz.Izvor.MAGACIN)
        UvozStavka.objects.create(uvoz=latest, product=new, artikal_naziv=new.naziv)
        site = Uvoz.objects.create(naziv='Sajt', izvor=Uvoz.Izvor.SAJT)
        UvozStavka.objects.create(uvoz=site, product=old, artikal_naziv=old.naziv)
        self.assertEqual(list(_latest_warehouse_import_products(Product.objects.all())), [new])
        Uvoz.objects.create(naziv='Prazan posljednji', izvor=Uvoz.Izvor.MAGACIN)
        self.assertFalse(_latest_warehouse_import_products(Product.objects.all()).exists())

    def test_no_import_has_no_new_products(self):
        Product.objects.create(naziv='Artikal', cijena=10)
        self.assertFalse(_latest_warehouse_import_products(Product.objects.all()).exists())

    def test_home_new_products_match_header_catalog_scope_and_order(self):
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        from .views import _home_latest_products_uncached, _apply_product_filters, _product_queryset, HOME_SECTION_PRODUCT_LIMIT
        old = Product.objects.create(naziv='Van posljednjeg uvoza', cijena=5, na_stanju=True, stanje=2)
        latest = Uvoz.objects.create(naziv='Posljednji', izvor=Uvoz.Izvor.MAGACIN)
        for index in range(12):
            product = Product.objects.create(naziv=f'Novi {index}', cijena=10+index, na_stanju=index != 0, stanje=0 if index == 0 else 2)
            UvozStavka.objects.create(uvoz=latest, product=product, artikal_naziv=product.naziv)
        request = RequestFactory().get('/', {'noviteti': '1'})
        request.user = AnonymousUser()
        catalog, _ = _apply_product_filters(_product_queryset(request), request)
        home = _home_latest_products_uncached(request)
        self.assertEqual([p.pk for p in home], [p.pk for p in catalog[:HOME_SECTION_PRODUCT_LIMIT]])
        self.assertNotIn(old.pk, [p.pk for p in home])
        Uvoz.objects.create(naziv='Prazan', izvor=Uvoz.Izvor.MAGACIN)
        self.assertEqual(_home_latest_products_uncached(request), [])
