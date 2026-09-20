from decimal import Decimal

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

    def test_checkout_reserves_and_only_picking_sells_once(self):
        from .views_magacin import _order_pick_bundle, apply_order_pick
        deduct_web_order_stock(self.order)
        deduct_web_order_stock(self.order)
        stock = WarehouseStock.objects.get(product=self.product)
        self.assertEqual((stock.kolicina, stock.rezervisano), (3, 2))
        self.assertFalse(WarehouseMovement.objects.filter(tip='prodaja').exists())
        self.assertFalse(self.order.stanje_skinuto)
        with self.assertRaises(MagacinError):
            validate_order_stock(self.order)
        queue = _order_pick_bundle(self.order)[0]
        apply_order_pick(self.order, [dict(row, got=row['need'], done=True) for row in queue], finalize=True)
        validate_order_stock(self.order)
        validate_order_stock(self.order)
        self.assertTrue(self.order.stanje_skinuto)
        stock.refresh_from_db()
        self.assertEqual((stock.kolicina, stock.rezervisano), (1, 0))
        self.assertEqual(WarehouseMovement.objects.filter(tip='prodaja').count(), 1)

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
        self.assertGreater(WarehouseMovement.objects.count(), before)

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
        with patch('EcommerceApp.emails.Thread') as thread, \
             patch('EcommerceApp.emails.send_order_emails') as email, \
             patch('EcommerceApp.views.sync_narudzba'), \
             patch('EcommerceApp.views.azuriraj_loyalty_nakon_narudzbe', return_value=None), \
             patch('EcommerceApp.views.track_purchase'), \
             patch('EcommerceApp.staff_alerts.notify_purchase'):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = checkout(request)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/narudzba/uspjeh/', response.url)
            saved = Order.objects.get(ime_prezime='Web Kupac')
            self.assertFalse(saved.stanje_skinuto)
            stock = WarehouseStock.objects.get(product=self.product)
            self.assertEqual((stock.kolicina, stock.rezervisano), (3, 2))
            self.assertFalse(request.session.get('cart'))
            email.assert_not_called()
            thread.assert_not_called()
            for callback in callbacks:
                callback()
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs['args'], (saved.pk,))
            thread.return_value.start.assert_called_once()
            email.assert_not_called()

    def test_partial_pick_reduces_webshop_total_and_xexpress_otkup(self):
        from .xexpress_service import _shipment_amounts

        apply_movement(product=self.product, location=self.location, tip='prijem', kolicina=10)
        item = self.order.stavke.get()
        item.kolicina = 10
        item.cijena = Decimal('10.00')
        item.save(update_fields=['kolicina', 'cijena'])
        self.order.medjuzbir = Decimal('100.00')
        self.order.dostava = Decimal('7.00')
        self.order.popust = Decimal('0.00')
        self.order.ukupno = Decimal('107.00')
        self.order.izvor = Order.Izvor.WEBSHOP
        self.order.save(update_fields=['medjuzbir', 'dostava', 'popust', 'ukupno', 'izvor'])
        deduct_web_order_stock(self.order)
        item.kolicina_pokupljeno = 8
        item.save(update_fields=['kolicina_pokupljeno'])
        validate_order_stock(self.order)
        self.order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 8)
        self.assertEqual(self.order.medjuzbir, Decimal('80.00'))
        self.assertEqual(self.order.dostava, Decimal('7.00'))
        self.assertEqual(self.order.ukupno, Decimal('87.00'))
        declared, pouz, otkup = _shipment_amounts(self.order)
        self.assertEqual(declared, 87.0)
        self.assertTrue(pouz)
        self.assertEqual(otkup, 87.0)

    def test_xexpress_uses_picked_qty_even_if_order_total_was_not_updated(self):
        from unittest.mock import Mock, patch

        from django.test import override_settings

        from .xexpress_service import create_shipment

        apply_movement(product=self.product, location=self.location, tip='prijem', kolicina=10)
        item = self.order.stavke.get()
        item.kolicina = 10
        item.cijena = Decimal('10.00')
        item.kolicina_pokupljeno = 8
        item.save(update_fields=['kolicina', 'cijena', 'kolicina_pokupljeno'])
        self.order.medjuzbir = Decimal('100.00')
        self.order.dostava = Decimal('0.00')
        self.order.ukupno = Decimal('100.00')
        self.order.izvor = Order.Izvor.WEBSHOP
        self.order.adresa = 'Ulica 1'
        self.order.grad = 'Sarajevo'
        self.order.telefon = '061000000'
        self.order.save()
        deduct_web_order_stock(self.order)
        fake = Mock()
        fake.status_code = 200
        fake.content = b'[{"sifra":"XE-PICK8"}]'
        fake.json.return_value = [{'sifra': 'XE-PICK8'}]
        missing_loc = Mock()
        missing_loc.status_code = 200
        missing_loc.content = b'[{"rb":0,"naziv":"Glavna adresa"}]'
        missing_loc.json.return_value = [{'rb': 0, 'naziv': 'Glavna adresa'}]
        with override_settings(
            XEXPRESS_USERNAME='xe-user',
            XEXPRESS_PASSWORD='xe-pass',
            XEXPRESS_LOKACIJA=0,
            XEXPRESS_REZERVACIJA=True,
        ):
            with patch('EcommerceApp.xexpress_service.requests.post', return_value=fake) as mocked:
                with patch('EcommerceApp.xexpress_service.requests.get', return_value=missing_loc):
                    create_shipment(self.order)
        body = mocked.call_args.kwargs.get('json') or mocked.call_args[1].get('json')
        self.assertEqual(body[0]['vrednostPosiljke'], 80.0)
        self.assertEqual(body[0]['iznosOtkupnine'], 80.0)
        self.order.refresh_from_db()
        self.assertEqual(self.order.ukupno, Decimal('80.00'))
        self.assertEqual(self.order.stavke.get().kolicina, 8)

    def test_zero_and_partial_pick_preserve_uncollected_stock(self):
        from .views_magacin import _order_pick_bundle, apply_order_pick
        from .models import LocationCleaningRequest
        for got in (0, 1):
            with self.subTest(got=got):
                product = Product.objects.create(naziv=f'Web {got}/2', cijena=10)
                apply_movement(product=product, location=self.location, tip='prijem', kolicina=2)
                order = Order.objects.create(ime_prezime='Kupac', ukupno=20)
                item = order.stavke.create(artikal=product, naziv=product.naziv, cijena=10, kolicina=2)
                deduct_web_order_stock(order)
                queue = _order_pick_bundle(order)[0]
                apply_order_pick(order, [dict(row, got=got, done=True) for row in queue], finalize=True)
                if got:
                    validate_order_stock(order)
                    item.refresh_from_db()
                    self.assertEqual(item.kolicina_faktura, got)
                stock = WarehouseStock.objects.get(product=product)
                self.assertEqual((stock.kolicina, stock.rezervisano), (2-got, 0))
                self.assertEqual(WarehouseMovement.objects.filter(product=product, tip='prodaja').count(), got)
                self.assertTrue(LocationCleaningRequest.objects.filter(order=order).exists())

    def test_zero_validation_never_sells_leftover_reservations(self):
        deduct_web_order_stock(self.order)
        self.order.stavke.update(kolicina_pokupljeno=0)
        validate_order_stock(self.order)
        stock = WarehouseStock.objects.get(product=self.product)
        self.assertEqual((stock.kolicina, stock.rezervisano), (3, 0))
        self.assertFalse(WarehouseMovement.objects.filter(tip='prodaja').exists())

    def test_legacy_checkout_deduction_is_restored_before_zero_pick(self):
        from .magacin import deduct_for_order, restore_unfinished_web_stock
        from .views_magacin import _order_pick_bundle, apply_order_pick
        deduct_for_order(self.product, 2, web_order=self.order, napomena=f'Web narudžba #{self.order.broj}')
        self.order.stanje_skinuto = True
        self.order.lager_status = Order.LagerStatus.REZERVISANO
        self.order.save(update_fields=['stanje_skinuto', 'lager_status'])
        queue = _order_pick_bundle(self.order)[0]
        apply_order_pick(self.order, [dict(row, got=0, done=True) for row in queue], finalize=True)
        restore_unfinished_web_stock(self.order)
        stock = WarehouseStock.objects.get(product=self.product)
        self.assertEqual((stock.kolicina, stock.rezervisano), (3, 0))
        self.order.refresh_from_db()
        self.assertFalse(self.order.stanje_skinuto)
        self.assertEqual(self.order.status, Order.Status.OTKAZANA)

    def test_legacy_partial_only_sells_physically_picked_quantity(self):
        from .magacin import deduct_for_order
        from .views_magacin import _order_pick_bundle, apply_order_pick
        deduct_for_order(self.product, 2, web_order=self.order, napomena=f'Web narudžba #{self.order.broj}')
        self.order.stanje_skinuto = True
        self.order.lager_status = Order.LagerStatus.REZERVISANO
        self.order.save(update_fields=['stanje_skinuto', 'lager_status'])
        queue = _order_pick_bundle(self.order)[0]
        apply_order_pick(self.order, [dict(row, got=1, done=True) for row in queue], finalize=True)
        validate_order_stock(self.order)
        validate_order_stock(self.order)
        stock = WarehouseStock.objects.get(product=self.product)
        self.assertEqual((stock.kolicina, stock.rezervisano), (2, 0))

    def test_reservation_prevents_another_order_buying_same_units(self):
        deduct_web_order_stock(self.order)
        other = Order.objects.create(ime_prezime='Drugi kupac', ukupno=20)
        other.stavke.create(artikal=self.product, naziv=self.product.naziv, cijena=10, kolicina=2)
        with self.assertRaises(MagacinError):
            deduct_web_order_stock(other)
        stock = WarehouseStock.objects.get(product=self.product)
        self.assertEqual((stock.kolicina, stock.rezervisano), (3, 2))
        self.assertFalse(other.magacin_holds.exists())

    def test_already_cancelled_legacy_zero_is_restored_once(self):
        from .magacin import deduct_for_order, restore_unfinished_web_stock
        deduct_for_order(self.product, 2, web_order=self.order, napomena=f'Web narudžba #{self.order.broj}')
        self.order.stanje_skinuto = True
        self.order.status = Order.Status.OTKAZANA
        self.order.lager_status = Order.LagerStatus.OTKAZANO
        self.order.save(update_fields=['stanje_skinuto', 'status', 'lager_status'])
        stale = Order.objects.get(pk=self.order.pk)
        restore_unfinished_web_stock(self.order)
        restore_unfinished_web_stock(stale)
        self.assertFalse(stale.stanje_skinuto)
        stock = WarehouseStock.objects.get(product=self.product)
        self.assertEqual((stock.kolicina, stock.rezervisano), (3, 0))
        self.assertFalse(self.order.stanje_skinuto)
        self.assertEqual(self.order.status, Order.Status.OTKAZANA)

    def test_legacy_explicit_cleaning_is_not_undone(self):
        from .magacin import deduct_for_order, restore_unfinished_web_stock
        from .models import LocationCleaningRequest
        deduct_for_order(self.product, 2, web_order=self.order, napomena=f'Web narudžba #{self.order.broj}')
        self.order.stanje_skinuto = True
        self.order.status = Order.Status.OTKAZANA
        self.order.lager_status = Order.LagerStatus.OTKAZANO
        self.order.save(update_fields=['stanje_skinuto', 'status', 'lager_status'])
        LocationCleaningRequest.objects.create(order=self.order, order_number=self.order.broj,
            item_id_snapshot=self.order.stavke.get().pk, product=self.product,
            location=self.location, name=self.product.naziv, needed=2, picked=0, decision='clear')
        apply_movement(product=self.product, location=self.location, tip='korekcija', kolicina=0)
        restore_unfinished_web_stock(self.order)
        self.assertEqual(WarehouseStock.objects.get(product=self.product).kolicina, 0)
