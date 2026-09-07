from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from .models import Product, Order, OrderItem, WarehouseStock, WarehouseLocation
from .cart import Cart
from .views import _magacin_stock_picks


class RequestPerformanceTests(TestCase):
    def setUp(self):
        self.products = [Product.objects.create(naziv=f'Perf {i}', cijena=10, stanje=5, na_stanju=True, magacin_sync_at=timezone.now()) for i in range(20)]
        self.location = WarehouseLocation.objects.create(sifra='FAST-A', naziv='Fast A')
        for product in self.products:
            WarehouseStock.objects.create(product=product, location=self.location, kolicina=5)

    def test_cart_query_scaling(self):
        request = RequestFactory().get('/korpa/')
        request.session = {'cart': {f'{p.pk}:0': {'product_id':p.pk,'quantity':1,'cijena':'10.00','bazna_cijena':'10.00','naziv':p.naziv} for p in self.products}}
        with CaptureQueriesContext(connection) as captured:
            items = list(Cart(request))
        self.assertLessEqual(len(captured), 7)
        print(f'CART_20_QUERIES={len(captured)}')
        self.assertEqual(len(items), 20)
        self.assertFalse(any(item['sold_out'] for item in items))

    def test_picking_query_scaling(self):
        order = Order.objects.create(ime_prezime='Performance', ukupno=200)
        for p in self.products:
            OrderItem.objects.create(narudzba=order, artikal=p, naziv=p.naziv, cijena=10, kolicina=1)
        items = list(order.stavke.select_related('artikal','varijacija'))
        with CaptureQueriesContext(connection) as captured:
            picks = _magacin_stock_picks(items)
        self.assertEqual(len(captured), 1)
        print(f'PICKING_20_QUERIES={len(captured)}')
        self.assertEqual(len(picks), 20)

    def test_batched_deals_preserve_priority_and_active_rules(self):
        from .models import Akcija, UpsellOffer
        from .upsell import prime_quantity_deals, get_quantity_deal
        first, second = self.products[:2]
        fallback = UpsellOffer.objects.create(deal_artikal=second, deal_vrsta='2+1', deal_popust=50)
        Akcija.objects.create(naziv='Aktivna', tip=Akcija.Tip.X_PLUS_1, artikal=first, deal_vrsta='2+1', popust_postotak=50)
        Akcija.objects.create(naziv='Neaktivna', aktivan=False, tip=Akcija.Tip.X_PLUS_1, artikal=first, deal_vrsta='2+1', popust_postotak=90)
        expected = {p.pk: get_quantity_deal(p) for p in self.products}
        with self.assertNumQueries(2):
            prime_quantity_deals(self.products)
        with self.assertNumQueries(0):
            actual = {p.pk: get_quantity_deal(p) for p in self.products}
        self.assertEqual(actual, expected)
        self.assertEqual(actual[second.pk], fallback)

    def test_duplicate_picking_lines_share_actual_available_quantity(self):
        order = Order.objects.create(ime_prezime='Duplicate', ukupno=100)
        for _ in range(2):
            OrderItem.objects.create(narudzba=order, artikal=self.products[0], naziv='Same SKU', cijena=10, kolicina=4)
        items = list(order.stavke.select_related('artikal','varijacija'))
        picks = _magacin_stock_picks(items)
        self.assertEqual(sum(pick['take'] for rows, _ in picks.values() for pick in rows), 5)
