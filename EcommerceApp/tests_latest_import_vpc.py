from decimal import Decimal
from django.test import TestCase
from .models import Product, Uvoz, UvozStavka
from .views_magacin import _latest_import_vpc


class LatestImportVpcTests(TestCase):
    def test_latest_valid_import_supplies_vpc_even_when_zero(self):
        product = Product.objects.create(naziv='Latest VPC article', cijena=20)
        old = Uvoz.objects.create(naziv='Old')
        UvozStavka.objects.create(uvoz=old, product=product, artikal_naziv=product.naziv, vpc_netto=10, status='updated')
        new = Uvoz.objects.create(naziv='New')
        line = UvozStavka.objects.create(uvoz=new, product=product, artikal_naziv=product.naziv, vpc_netto=12, status='updated')
        self.assertEqual(_latest_import_vpc(product), Decimal('12'))
        UvozStavka.objects.create(uvoz=new, product=product, artikal_naziv=product.naziv, vpc_netto=99, status='error')
        self.assertEqual(_latest_import_vpc(product), Decimal('12'))
        line.vpc_netto = 0
        line.save()
        self.assertEqual(_latest_import_vpc(product), Decimal('0'))

    def test_unlinked_name_can_match_but_another_products_price_cannot(self):
        product = Product.objects.create(naziv='Same name', cijena=20)
        other = Product.objects.create(naziv='Same name', cijena=30)
        entry = Uvoz.objects.create(naziv='Import')
        UvozStavka.objects.create(uvoz=entry, product=other, artikal_naziv=product.naziv, vpc_netto=99)
        self.assertIsNone(_latest_import_vpc(product))
        UvozStavka.objects.create(uvoz=entry, artikal_naziv=product.naziv, vpc_netto=15)
        self.assertEqual(_latest_import_vpc(product), Decimal('15'))
