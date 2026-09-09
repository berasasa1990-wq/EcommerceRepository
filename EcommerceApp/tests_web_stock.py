from django.test import TestCase
from .models import Product, ProductVariation, Order, OrderItem, WarehouseLocation, WarehouseStock, WarehouseMovement
from .magacin import apply_movement, deduct_web_order_stock, validate_order_stock, MagacinError


class WebStockTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Web artikal', cijena=10, stanje=0)
        self.location = WarehouseLocation.objects.create(sifra='A04', naziv='A04')
        apply_movement(product=self.product, location=self.location, tip='prijem', kolicina=3)
        self.order = Order.objects.create(ime_prezime='Kupac', ukupno=20)
        OrderItem.objects.create(narudzba=self.order, artikal=self.product, naziv='Web artikal', cijena=10, kolicina=2)

    def test_sale_is_immediate_and_not_repeated_on_validation(self):
        deduct_web_order_stock(self.order)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 1)
        self.assertEqual(WarehouseStock.objects.get(product=self.product).kolicina, 1)
        self.assertTrue(WarehouseMovement.objects.filter(napomena=f'Web narudžba #{self.order.broj}', kolicina=-2).exists())
        stale = Order.objects.get(pk=self.order.pk)
        deduct_web_order_stock(stale)
        validate_order_stock(stale)
        self.assertEqual(WarehouseStock.objects.get(product=self.product).kolicina, 1)

    def test_sold_out_web_order_remains_on_picking_at_original_location(self):
        from .views_magacin import _order_pick_bundle, apply_order_pick
        OrderItem.objects.filter(narudzba=self.order).update(kolicina=3)
        deduct_web_order_stock(self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.lager_status, Order.LagerStatus.REZERVISANO)
        self.assertEqual(self.order.status, Order.Status.NOVA)
        self.assertFalse(self.order.zapakovana)
        queue, _, _ = _order_pick_bundle(self.order)
        self.assertEqual(sum(row['need'] for row in queue), 3)
        self.assertEqual(queue[0]['loc'], 'A04')
        before = WarehouseMovement.objects.count()
        apply_order_pick(self.order, [dict(row, got=row['need'], done=True) for row in queue], finalize=True)
        validate_order_stock(self.order)
        self.order.refresh_from_db()
        self.assertTrue(self.order.zapakovana)
        self.assertEqual(self.order.lager_status, Order.LagerStatus.VALIDIRANO)
        self.assertEqual(WarehouseStock.objects.get(product=self.product).kolicina, 0)
        self.assertEqual(WarehouseMovement.objects.count(), before)

    def test_insufficient_stock_rolls_back_whole_sale(self):
        extra = Product.objects.create(naziv='Nema', cijena=5, stanje=0, na_stanju=False)
        OrderItem.objects.create(narudzba=self.order, artikal=extra, naziv='Nema', cijena=5, kolicina=1)
        with self.assertRaises(MagacinError):
            deduct_web_order_stock(self.order)
        self.assertEqual(WarehouseStock.objects.get(product=self.product).kolicina, 3)
        self.assertFalse(WarehouseMovement.objects.filter(napomena=f'Web narudžba #{self.order.broj}').exists())
        self.order.refresh_from_db()
        self.assertFalse(self.order.stanje_skinuto)

    def test_last_unit_and_catalog_variation(self):
        OrderItem.objects.filter(narudzba=self.order).update(kolicina=3)
        deduct_web_order_stock(self.order)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 0)
        self.assertFalse(self.product.na_stanju)
        product = Product.objects.create(naziv='Varijacije', cijena=10, stanje=2, na_stanju=True)
        variation = ProductVariation.objects.create(artikal=product, naziv='M', cijena=10, stanje=2, na_stanju=True)
        order = Order.objects.create(ime_prezime='Kupac', ukupno=20)
        OrderItem.objects.create(narudzba=order, artikal=product, varijacija=variation, naziv='M', cijena=10, kolicina=2)
        deduct_web_order_stock(order)
        variation.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(variation.stanje, 0)
        self.assertFalse(variation.na_stanju)
        self.assertFalse(product.na_stanju)

    def test_checkout_commits_stock_before_confirmation(self):
        from unittest.mock import patch
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        from django.contrib.messages.storage.fallback import FallbackStorage
        from .views import checkout
        request = RequestFactory().post('/narudzba/', {
            'ime_prezime': 'Web Kupac', 'email': 'kupac@example.com',
            'telefon': '061234567', 'adresa': 'Test 1', 'grad': 'Sarajevo',
        })
        request.user = AnonymousUser()
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:0': {
            'product_id': self.product.pk, 'quantity': 2, 'cijena': '10.00',
            'bazna_cijena': '10.00', 'na_akciji': False, 'naziv': self.product.naziv, 'sifra': 'WEB-1',
        }}
        session.save()
        request.session = session
        request._messages = FallbackStorage(request)
        def confirm(order):
            self.assertTrue(order.stanje_skinuto)
            self.assertEqual(WarehouseStock.objects.get(product=self.product).kolicina, 1)
        with patch('EcommerceApp.views.send_order_emails', side_effect=confirm) as email, \
             patch('EcommerceApp.views.sync_narudzba'), \
             patch('EcommerceApp.views.azuriraj_loyalty_nakon_narudzbe', return_value=None), \
             patch('EcommerceApp.views.track_purchase'), \
             patch('EcommerceApp.staff_alerts.notify_purchase'):
            response = checkout(request)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/narudzba/uspjeh/', response.url)
        email.assert_called_once()
