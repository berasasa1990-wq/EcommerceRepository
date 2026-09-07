from django.test import TestCase
from .models import Product, ProductVariation, WarehouseLocation, WarehouseStock, Order, OrderItem, OrderStockHold
from .magacin import exact_order_location_rows, reserve_for_order
from .views import _magacin_hold_picks, _magacin_stock_picks


class ExactPickingLocationTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Varalica', cijena=10)
        self.variant = ProductVariation.objects.create(artikal=self.product, naziv='Crvena')
        self.other = ProductVariation.objects.create(artikal=self.product, naziv='Plava')
        self.a03 = WarehouseLocation.objects.create(sifra='A03', naziv='A03')
        self.a05 = WarehouseLocation.objects.create(sifra='A05', naziv='A05')
        self.order = Order.objects.create(ime_prezime='Kupac', ukupno=20)
        self.item = OrderItem.objects.create(narudzba=self.order, artikal=self.product, varijacija=self.variant, naziv='Crvena', cijena=10, kolicina=2)

    def test_relocation_ignores_parent_and_other_variant(self):
        WarehouseStock.objects.create(product=self.product, location=self.a03, kolicina=10)
        WarehouseStock.objects.create(product=self.product, variation=self.other, location=self.a03, kolicina=5)
        self.assertEqual(exact_order_location_rows(self.product, self.variant), [])
        self.assertEqual(reserve_for_order(self.order, self.product, 1, variation=self.variant, exact=True), 1)
        self.assertFalse(self.order.magacin_holds.exists())
        self.assertEqual(_magacin_stock_picks([self.item], exact_items={self.item.pk}), {})
        WarehouseStock.objects.create(product=self.product, variation=self.variant, location=self.a05, kolicina=1)
        self.assertEqual(reserve_for_order(self.order, self.product, 1, variation=self.variant, exact=True), 0)
        self.assertEqual(self.order.magacin_holds.get().location, self.a05)

    def test_empty_location_is_not_offered_from_stale_reservation(self):
        stock = WarehouseStock.objects.create(product=self.product, variation=self.variant, location=self.a03, kolicina=0, rezervisano=2)
        OrderStockHold.objects.create(narudzba=self.order, product=self.product, variation=self.variant, location=self.a03, kolicina=2)
        self.assertEqual(_magacin_hold_picks(self.order, [self.item]), {})
        stock.kolicina = 1
        stock.save()
        picks, missing = _magacin_hold_picks(self.order, [self.item])[self.item.pk]
        self.assertEqual(picks[0]['take'], 1)
        self.assertEqual(missing, 1)

    def test_normal_picking_requires_exact_variant_stock(self):
        WarehouseStock.objects.create(product=self.product, location=self.a03, kolicina=10)
        WarehouseStock.objects.create(product=self.product, variation=self.other, location=self.a03, kolicina=5)
        OrderStockHold.objects.create(narudzba=self.order, product=self.product, location=self.a03, kolicina=2)
        self.assertEqual(_magacin_stock_picks([self.item]), {})
        self.assertEqual(_magacin_hold_picks(self.order, [self.item]), {})
        WarehouseStock.objects.create(product=self.product, variation=self.variant, location=self.a05, kolicina=1)
        picks, missing = _magacin_stock_picks([self.item])[self.item.pk]
        self.assertEqual([(p['location_id'], p['take']) for p in picks], [(self.a05.pk, 1)])
        self.assertEqual(missing, 1)

    def test_empty_local_stock_does_not_fall_back_to_remote_locations(self):
        from unittest.mock import patch
        from .views import _build_order_packing_lines
        with patch('EcommerceApp.odoo_client.odoo_je_konfigurisan', return_value=True), patch('EcommerceApp.odoo_client.OdooClient.from_settings') as client:
            lines, _ = _build_order_packing_lines(self.order)
        client.assert_not_called()
        self.assertFalse(any(p.get('location_id') for p in lines[0]['picks']))
