import json
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Product, ProductVariation, ProductSetComponent, Order, OrderItem, WarehouseLocation, WarehouseStock, WarehouseMovement
from .magacin import (MagacinError, apply_movement, reserve_web_order_stock, reserve_for_order, cancel_order_stock,
                      validate_order_stock, display_stock_totals, add_item_to_order, set_order_item_qty, remove_item_from_order)
from .product_sets import save_set, set_stock_totals
from .views_magacin import _order_pick_bundle, apply_order_pick


@override_settings(SECURE_SSL_REDIRECT=False, SITE_PREP_ENABLED=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ProductSetTests(TestCase):
    def setUp(self):
        self.location = WarehouseLocation.objects.create(sifra='SET-A', naziv='SET-A')
        self.rod = Product.objects.create(naziv='Štap', cijena=40)
        self.hook = Product.objects.create(naziv='Udica', cijena=5)
        apply_movement(product=self.rod, location=self.location, tip='prijem', kolicina=5)
        apply_movement(product=self.hook, location=self.location, tip='prijem', kolicina=8)
        self.rows = [{'product_id': self.rod.pk, 'quantity': 1}, {'product_id': self.hook.pk, 'quantity': 2}]
        self.product = save_set(name='Komplet za ribolov', code='SET-1', regular_price='60', sale_price='45', rows=self.rows)

    def order(self, qty=2, source=Order.Izvor.WEBSHOP):
        order = Order.objects.create(ime_prezime='Kupac seta', telefon='061123456', adresa='Adresa', grad='Sarajevo',
                                     ukupno=45 * qty, medjuzbir=45 * qty, izvor=source)
        parent = OrderItem.objects.create(narudzba=order, artikal=self.product, naziv=self.product.naziv,
                                         cijena=45, bazna_cijena=60, kolicina=qty)
        return order, parent

    def test_stock_is_minimum_complete_sets_and_updates_when_component_changes(self):
        self.assertEqual(self.product.prikazna_cijena, Decimal('45'))
        self.assertEqual(display_stock_totals(self.product)['dostupno'], 4)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 4)
        apply_movement(product=self.hook, location=self.location, tip='korekcija', kolicina=1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 0)
        self.assertFalse(self.product.na_stanju)
        apply_movement(product=self.hook, location=self.location, tip='prijem', kolicina=5)
        self.product.refresh_from_db()
        self.assertTrue(self.product.na_stanju)
        self.assertEqual(self.product.stanje, 3)
        with self.assertRaises(MagacinError):
            apply_movement(product=self.product, location=self.location, tip='prijem', kolicina=1)

    def test_buy_set_picks_and_deducts_components_once_at_set_price(self):
        order, parent = self.order()
        reserve_web_order_stock(order)
        reserve_web_order_stock(order)
        self.assertEqual(WarehouseStock.objects.get(product=self.rod).rezervisano, 2)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 4)
        self.assertFalse(WarehouseStock.objects.filter(product=self.product).exists())
        children = list(parent.set_children.order_by('artikal_id'))
        self.assertEqual([c.kolicina for c in children], [2, 4])
        self.assertTrue(all(c.kolicina_faktura == 0 for c in children))
        queue = _order_pick_bundle(order)[0]
        self.assertEqual({r['item_id']: r['need'] for r in queue}, {c.pk: c.kolicina for c in children})
        apply_order_pick(order, [dict(row, got=row['need'], done=True) for row in queue], finalize=True)
        validate_order_stock(order)
        validate_order_stock(order)
        self.assertEqual(WarehouseStock.objects.get(product=self.rod).kolicina, 3)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).kolicina, 4)
        self.assertEqual(WarehouseMovement.objects.filter(tip='prodaja').count(), 2)
        order.refresh_from_db()
        parent.refresh_from_db()
        self.assertEqual(parent.ukupno, Decimal('90'))
        self.assertEqual(order.medjuzbir, Decimal('90'))

    def test_insufficient_shared_stock_rolls_back_all_reservations(self):
        order, parent = self.order(qty=5)
        with self.assertRaises(MagacinError):
            reserve_web_order_stock(order)
        self.assertEqual(WarehouseStock.objects.get(product=self.rod).rezervisano, 0)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 0)
        self.assertFalse(order.magacin_holds.exists())
        self.assertFalse(parent.set_children.exists())

    def test_order_components_survive_set_edit_and_cancellation_restores_availability(self):
        order, parent = self.order()
        reserve_web_order_stock(order)
        save_set(product_id=self.product.pk, name='Komplet izmijenjen', regular_price=70, rows=[self.rows[0]])
        self.assertEqual(parent.set_children.get(artikal=self.hook).kolicina, 4)
        cancel_order_stock(order)
        self.assertEqual(WarehouseStock.objects.get(product=self.rod).rezervisano, 0)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 0)
        self.assertEqual(set_stock_totals(self.product)['dostupno'], 5)

    def test_partial_set_cannot_finish_picking_or_validation(self):
        order, parent = self.order()
        reserve_web_order_stock(order)
        queue = _order_pick_bundle(order)[0]
        with self.assertRaises(MagacinError):
            apply_order_pick(order, [dict(row, got=0, done=True) for row in queue], finalize=True)
        with self.assertRaises(MagacinError):
            validate_order_stock(order)
        self.assertEqual(parent.set_children.count(), 2)
        self.assertFalse(WarehouseMovement.objects.filter(tip='prodaja').exists())

    def test_variation_quantity_is_used(self):
        variant_product = Product.objects.create(naziv='Najlon', cijena=10)
        variation = ProductVariation.objects.create(artikal=variant_product, naziv='0.20', cijena=10)
        apply_movement(product=variant_product, variation=variation, location=self.location, tip='prijem', kolicina=6)
        save_set(product_id=self.product.pk, name='Set', regular_price=60,
                 rows=[{'product_id':variant_product.pk, 'variation_id':variation.pk, 'quantity':3}])
        order, parent = self.order(qty=2)
        reserve_web_order_stock(order)
        child = parent.set_children.get()
        self.assertEqual(child.varijacija, variation)
        self.assertEqual(child.kolicina, 6)
        self.assertEqual(set_stock_totals(self.product)['dostupno'], 0)

    def test_management_create_and_edit_form(self):
        user = User.objects.create_superuser('set-admin', 'sets@example.com', 'pass')
        self.client.force_login(user)
        url = reverse('staff_magacin_setovi')
        response = self.client.get(url, {'id':self.product.pk})
        self.assertContains(response, 'Artikli u setu')
        self.assertContains(response, 'setComponentsJson')
        result = self.client.post(url, {'naziv':'Novi set', 'cijena':'100', 'akcijska_cijena':'80',
                                       'components_json':json.dumps(self.rows), 'aktivan':'1'})
        self.assertEqual(result.status_code, 302)
        saved = Product.objects.get(naziv='Novi set')
        self.assertTrue(saved.is_set)
        self.assertEqual(saved.set_components.count(), 2)
        self.assertEqual(saved.stanje, 4)

    def test_invalid_duplicate_and_nested_components_are_rejected(self):
        with self.assertRaises(MagacinError):
            save_set(name='Dupli', regular_price=10, rows=[self.rows[0], self.rows[0]])
        with self.assertRaises(MagacinError):
            save_set(name='Ugniježden', regular_price=10, rows=[{'product_id':self.product.pk, 'quantity':1}])
        with self.assertRaises(MagacinError):
            save_set(name='Nema', regular_price=10, rows=[])

    def test_manual_set_quantity_edits_update_component_holds(self):
        order = Order.objects.create(ime_prezime='Kupac', ukupno=0, izvor=Order.Izvor.MAGACIN)
        parent = add_item_to_order(order, product=self.product, qty=1)
        set_order_item_qty(order, parent, 2)
        self.assertEqual(parent.set_children.get(artikal=self.hook).kolicina, 4)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 4)
        set_order_item_qty(order, parent, 1)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 2)
        extra = add_item_to_order(order, product=self.rod, qty=1)
        parent_id = parent.pk
        remove_item_from_order(order, parent)
        self.assertFalse(OrderItem.objects.filter(set_parent=parent_id).exists())
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 0)
        self.assertEqual(WarehouseStock.objects.get(product=self.rod).rezervisano, 1)

    def test_set_is_searchable_without_own_physical_stock(self):
        user = User.objects.create_superuser('set-search', 'search@example.com', 'pass')
        self.client.force_login(user)
        response = self.client.get(reverse('staff_magacin_artikli_lookup'), {'q': self.product.naziv})
        result = response.json()['results'][0]
        self.assertEqual(result['id'], self.product.pk)
        self.assertTrue(result['is_set'])
        self.assertEqual(result['dostupno'], 4)

    def test_manual_order_form_edit_preserves_snapshot_and_reservations(self):
        user = User.objects.create_superuser('manual-sets', 'manual@example.com', 'pass')
        self.client.force_login(user)
        data = {'ime_prezime':'Kupac', 'telefon':'061123456', 'adresa':'Adresa', 'grad':'Sarajevo',
                'product_id':[str(self.product.pk)], 'variation_id':[''], 'kolicina':['2'], 'mp_ok':['0']}
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.MAGACIN)
        parent = order.stavke.get(is_set_parent=True)
        snapshot_ids = list(parent.set_children.values_list('pk', flat=True))
        data['order_broj'] = order.broj
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(parent.set_children.values_list('pk', flat=True)), snapshot_ids)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 4)
        self.assertEqual(WarehouseStock.objects.get(product=self.rod).rezervisano, 2)

    def test_web_checkout_creates_set_invoice_and_component_picking(self):
        from contextlib import ExitStack
        from unittest.mock import patch
        session = self.client.session
        session['cart'] = {f'{self.product.pk}:0': {'product_id':self.product.pk, 'variation_id':None,
            'quantity':2, 'cijena':'45.00', 'bazna_cijena':'60.00', 'na_akciji':True,
            'naziv':self.product.naziv, 'product_naziv':self.product.naziv, 'sifra':'SET-1'}}
        session.save()
        with ExitStack() as stack:
            for name in ('queue_order_emails', 'sync_narudzba', 'track_purchase', 'azuriraj_loyalty_nakon_narudzbe'):
                stack.enter_context(patch('EcommerceApp.views.' + name))
            stack.enter_context(patch('EcommerceApp.staff_alerts.notify_purchase'))
            response = self.client.post(reverse('checkout'), {
                'ime_prezime':'Kupac seta', 'telefon':'061123456', 'email':'sets@example.com',
                'adresa':'Ulica 1', 'grad':'Sarajevo', 'postanski_broj':'71000', 'payment_method':'cod',
            })
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(izvor=Order.Izvor.WEBSHOP)
        parent = order.stavke.get(is_set_parent=True)
        self.assertEqual(parent.cijena, Decimal('45'))
        self.assertEqual(parent.kolicina, 2)
        self.assertEqual(parent.set_children.get(artikal=self.hook).kolicina, 4)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 4)
        queue = _order_pick_bundle(order)[0]
        self.assertEqual(sum(row['need'] for row in queue), 6)

    def test_separate_set_lines_release_only_the_selected_sets_components(self):
        order, first = self.order(qty=1, source=Order.Izvor.MAGACIN)
        reserve_for_order(order, self.product, 1, set_item=first)
        second = OrderItem.objects.create(narudzba=order, artikal=self.product, naziv='Drugi set', cijena=45, kolicina=1)
        reserve_for_order(order, self.product, 1, set_item=second)
        first.refresh_from_db()
        remove_item_from_order(order, first)
        self.assertEqual(second.set_children.count(), 2)
        self.assertEqual(WarehouseStock.objects.get(product=self.rod).rezervisano, 1)
        self.assertEqual(WarehouseStock.objects.get(product=self.hook).rezervisano, 2)

    def test_saved_sets_show_component_stock_and_free_quantities(self):
        order, _ = self.order()
        reserve_web_order_stock(order)
        user = User.objects.create_superuser('stock-sets', 'stock@example.com', 'pass')
        self.client.force_login(user)
        response = self.client.get(reverse('staff_magacin_setovi'))
        self.assertContains(response, 'Stanje artikala iz seta Komplet za ribolov')
        self.assertContains(response, 'U jednom setu')
        rows = response.context['sets'][0].component_stock_rows
        stocks = {row['component'].product_id: row for row in rows}
        self.assertEqual((stocks[self.rod.pk]['na_stanju'], stocks[self.rod.pk]['dostupno']), (5, 3))
        self.assertEqual((stocks[self.hook.pk]['na_stanju'], stocks[self.hook.pk]['dostupno']), (8, 4))
        self.assertEqual(stocks[self.hook.pk]['component'].quantity, 2)
