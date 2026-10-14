from django.test import TestCase
from .models import Product, Order, OrderItem
from .views import _featured_catalog_qs


class WebshopBestsellerTests(TestCase):
    def test_quantity_ranking_excludes_warehouse_cancelled_and_unpaid_orders(self):
        most = Product.objects.create(naziv='Najviše', cijena=10, na_stanju=True, stanje=5)
        other = Product.objects.create(naziv='Drugi', cijena=10, na_stanju=True, stanje=5, je_hit=True)
        for index, (product, quantity, source, status) in enumerate([
            (most, 8, Order.Izvor.WEBSHOP, Order.Status.NOVA),
            (most, 4, Order.Izvor.WEBSHOP, Order.Status.ZAVRSENA),
            (other, 3, Order.Izvor.WEBSHOP, Order.Status.POTVRDJENA),
            (other, 100, Order.Izvor.MAGACIN, Order.Status.ZAVRSENA),
            (other, 100, Order.Izvor.WEBSHOP, Order.Status.OTKAZANA),
            (other, 100, Order.Izvor.WEBSHOP, Order.Status.CEKA_PLACANJE),
        ]):
            order = Order.objects.create(ime_prezime=f'Kupac {index}', izvor=source, status=status, ukupno=quantity * 10)
            OrderItem.objects.create(narudzba=order, artikal=product, naziv=product.naziv, kolicina=quantity, cijena=10)
        products = list(_featured_catalog_qs(Product.objects.all()))
        self.assertEqual([p.pk for p in products], [most.pk, other.pk])
        self.assertEqual([p.webshop_ordered_quantity for p in products], [12, 3])

    def test_featured_products_exclude_unavailable_and_zero_stock(self):
        from .views import _home_featured_products_uncached
        available = Product.objects.create(naziv='Dostupan', cijena=10, na_stanju=True, stanje=5)
        unavailable = Product.objects.create(naziv='Nedostupan', cijena=10, na_stanju=False, stanje=5)
        empty = Product.objects.create(naziv='Bez količine', cijena=10, na_stanju=True, stanje=0)
        order = Order.objects.create(ime_prezime='Kupac', izvor=Order.Izvor.WEBSHOP, status=Order.Status.ZAVRSENA, ukupno=100)
        for product, quantity in [(available, 1), (unavailable, 8), (empty, 9)]:
            OrderItem.objects.create(narudzba=order, artikal=product, naziv=product.naziv, kolicina=quantity, cijena=10)
        self.assertEqual(list(_featured_catalog_qs(Product.objects.all()).values_list('pk', flat=True)), [available.pk])
        self.assertEqual([p.pk for p in _home_featured_products_uncached()], [available.pk])
