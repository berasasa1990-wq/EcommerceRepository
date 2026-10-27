from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Product, WarehouseLocation, WarehouseStock, Order
from .magacin import apply_movement, validate_order_stock
from .views_magacin import _order_pick_bundle, apply_order_pick, confirm_short_pick

@override_settings(ALLOWED_HOSTS=['testserver'], SITE_PREP_ENABLED=False,
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PickQuantityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('picker', 'p@example.com', 'secret')
        self.product = Product.objects.create(naziv='Picking 3 kom', cijena=Decimal('10'), magacin_sync_at=timezone.now())
        self.location = WarehouseLocation.objects.create(sifra='PICK-A', naziv='Polica')
        apply_movement(product=self.product, location=self.location, tip='prijem', kolicina=10)
        self.client.force_login(self.user)
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Picking kupac', 'telefon': '061123456',
            'product_id': [self.product.pk], 'variation_id': [''], 'kolicina': ['3'], 'mp_ok': ['0']})
        self.assertEqual(response.status_code, 302)
        self.order = Order.objects.get(ime_prezime='Picking kupac')
        self.item = self.order.stavke.get()
        self.line = _order_pick_bundle(self.order)[0][0]

    def stock(self):
        return WarehouseStock.objects.get(product=self.product, location=self.location).kolicina

    def test_pick_all_means_three_not_ten(self):
        self.assertEqual(self.line['need'], 3)
        self.assertEqual(self.line['on_hand'], 10)
        apply_order_pick(self.order, [dict(self.line, got=3, done=True)], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        self.assertEqual(self.stock(), 7)
        self.item.refresh_from_db()
        self.assertEqual(self.item.kolicina_faktura, 3)

    def test_positive_partial_without_clear_keeps_location(self):
        confirm_short_pick(self.order, item_id=self.item.pk, loc=self.location.sifra,
                           got=1, user=self.user)
        self.assertEqual(self.stock(), 10)
        self.assertEqual(self.order.pick_short_events, [])
        apply_order_pick(self.order, [dict(self.line, got=1, done=True)], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        self.assertEqual(self.stock(), 9)
        self.item.refresh_from_db()
        self.assertEqual(self.item.kolicina_faktura, 1)

    def test_zero_without_confirmation_preserves_stock_even_on_finalize(self):
        confirm_short_pick(self.order, item_id=self.item.pk, loc=self.location.sifra, got=0, user=self.user)
        self.assertEqual(self.stock(), 10)
        apply_order_pick(self.order, [dict(self.line, got=0, done=True)], finalize=True, user=self.user)
        self.assertEqual(self.stock(), 10)

    def test_picking_offers_all_stock_locations_maloprodaja_last(self):
        a04 = WarehouseLocation.objects.create(sifra='A-04', naziv='Polica A04')
        z01 = WarehouseLocation.objects.create(sifra='Z-01', naziv='Polica Z01')
        mp = WarehouseLocation.objects.create(sifra='Maloprodaja', naziv='Maloprodaja')
        product = Product.objects.create(
            naziv='Sve lokacije', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a04, tip='prijem', kolicina=1)
        apply_movement(product=product, location=z01, tip='prijem', kolicina=1)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=2)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Sve lokacije', 'telefon': '061333333',
            'product_id': [product.pk], 'variation_id': [''], 'kolicina': ['3'], 'mp_ok': ['0']})
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Sve lokacije')
        item = order.stavke.get()
        queue = _order_pick_bundle(order)[0]
        self.assertEqual(
            [(row['loc'], row['need'], bool(row.get('is_mp'))) for row in queue],
            [('A-04', 1, False), ('Z-01', 1, False), ('Maloprodaja', 1, True)],
        )
    def test_picked_quantity_can_exceed_order_quantity(self):
        apply_order_pick(self.order, [dict(self.line, got=10, need=10, done=True)], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        self.assertEqual(self.stock(), 0)
        self.item.refresh_from_db()
        self.assertEqual(self.item.kolicina_faktura, 10)

    def _make_order(self, name, product, qty, phone):
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': name, 'telefon': phone,
            'product_id': [product.pk], 'variation_id': [''], 'kolicina': [str(qty)], 'mp_ok': ['0']})
        self.assertEqual(created.status_code, 302)
        return Order.objects.get(ime_prezime=name)

    def test_allocation_warehouse_first_mp_last_covers_full_qty(self):
        a01 = WarehouseLocation.objects.create(sifra='A-01', naziv='Polica A01')
        b04 = WarehouseLocation.objects.create(sifra='B-04', naziv='Polica B04')
        mp = WarehouseLocation.objects.create(sifra='Maloprodaja', naziv='Maloprodaja')
        product = Product.objects.create(
            naziv='Pet komada', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a01, tip='prijem', kolicina=2)
        apply_movement(product=product, location=b04, tip='prijem', kolicina=2)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=3)
        order = self._make_order('Pet komada', product, 5, '061555001')
        queue = _order_pick_bundle(order)[0]
        self.assertEqual(
            [(row['loc'], row['need'], bool(row.get('is_mp'))) for row in queue],
            [('A-01', 2, False), ('B-04', 2, False), ('Maloprodaja', 1, True)],
        )

    def test_less_or_more_uses_actual_invoice_and_print_quantity_without_cleaning(self):
        import json
        from .models import LocationCleaningRequest, WarehouseMovement
        from .pricing import pripremi_stavke_za_racun
        for got in (1, 2, 5):
            with self.subTest(got=got):
                product = Product.objects.create(naziv=f'Faktura {got}', cijena=10, magacin_sync_at=timezone.now())
                apply_movement(product=product, location=self.location, tip='prijem', kolicina=10)
                order = self._make_order(product.naziv, product, 3, '061555001')
                line = _order_pick_bundle(order)[0][0]
                response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
                    'action': 'validiraj', 'pick_json': json.dumps([dict(line, got=got, done=False)])})
                self.assertEqual(response.status_code, 302)
                order.refresh_from_db()
                self.assertEqual(order.lager_status, Order.LagerStatus.VALIDIRANO)
                self.assertEqual(order.stavke.get().kolicina_faktura, got)
                self.assertEqual(pripremi_stavke_za_racun(order)[0]['kolicina'], got)
                self.assertEqual(order.medjuzbir, Decimal(10 * got))
                printed = self.client.get(reverse('staff_magacin_narudzbe_stampa_kolicine'), {'b': order.broj})
                self.assertEqual(printed.context['print_jobs'][0]['stavke'][0]['kolicina'], got)
                self.assertFalse(LocationCleaningRequest.objects.filter(order=order).exists())
                self.assertFalse(WarehouseMovement.objects.filter(product=product, tip='korekcija').exists())
                self.assertEqual(WarehouseStock.objects.get(product=product, location=self.location).kolicina, 10-got)

    def test_zero_line_removed_from_print_without_clearing_stock(self):
        from .models import LocationCleaningRequest
        other = Product.objects.create(naziv='Drugi artikal', cijena=10, magacin_sync_at=timezone.now())
        apply_movement(product=other, location=self.location, tip='prijem', kolicina=2)
        self.order.stavke.create(artikal=other, naziv=other.naziv, kolicina=1, cijena=10)
        queue = _order_pick_bundle(self.order)[0]
        apply_order_pick(self.order, [dict(row, got=0 if row['item_id'] == self.item.pk else row['need'], done=True) for row in queue], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        self.assertFalse(self.order.stavke.filter(pk=self.item.pk).exists())
        self.assertFalse(LocationCleaningRequest.objects.filter(order=self.order, product=self.product).exists())
        printed = self.client.get(reverse('staff_magacin_narudzbe_stampa_kolicine'), {'b': self.order.broj})
        self.assertNotContains(printed, self.product.naziv)
        self.assertContains(printed, other.naziv)
        self.assertEqual(self.stock(), 10)

    def test_print_mp_only_when_actually_picked_from_retail(self):
        mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        product = Product.objects.create(naziv='MP štampa', cijena=10, magacin_sync_at=timezone.now())
        apply_movement(product=product, location=mp, tip='prijem', kolicina=2)
        order = self._make_order('MP štampa', product, 2, '061555003')
        queue = _order_pick_bundle(order)[0]
        apply_order_pick(order, [dict(row, got=1, done=True) for row in queue], finalize=True, user=self.user)
        validate_order_stock(order, user=self.user)
        printed = self.client.get(reverse('staff_magacin_narudzbe_stampa_kolicine'), {'b': order.broj})
        self.assertContains(printed, '<strong>MP</strong>')
        self.assertEqual(printed.context['print_jobs'][0]['stavke'][0]['kolicina'], 1)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=mp).kolicina, 1)

    def test_clear_waits_for_pick_deduction_and_keep_is_final(self):
        from .location_cleaning import resolve_request
        from .models import LocationCleaningRequest
        from .magacin import MagacinError
        confirm_short_pick(self.order, item_id=self.item.pk, loc=self.line['loc'], got=1, user=self.user)
        self.assertFalse(LocationCleaningRequest.objects.filter(order=self.order).exists())
        entry = LocationCleaningRequest.objects.create(order=self.order, order_number=self.order.broj,
            item_id_snapshot=self.item.pk, product=self.product, location=self.location,
            name=self.product.naziv, needed=3, picked=1)
        with self.assertRaises(MagacinError):
            resolve_request(entry.pk, clear=True, user=self.user)
        self.assertEqual(self.stock(), 10)
        resolve_request(entry.pk, clear=False, user=self.user)
        apply_order_pick(self.order, [dict(self.line, got=1, done=True)], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        resolve_request(entry.pk, clear=True, user=self.user)
        self.assertEqual(self.stock(), 9)
        self.assertEqual(LocationCleaningRequest.objects.filter(order=self.order).count(), 1)

    def test_clear_only_affects_requested_variant_and_location(self):
        from .models import ProductVariation, LocationCleaningRequest
        from .location_cleaning import resolve_request
        variant = ProductVariation.objects.create(artikal=self.product, naziv='Crvena')
        other = ProductVariation.objects.create(artikal=self.product, naziv='Plava')
        elsewhere = WarehouseLocation.objects.create(sifra='OTHER', naziv='Druga polica')
        for variation, location in [(variant, self.location), (other, self.location), (variant, elsewhere)]:
            apply_movement(product=self.product, variation=variation, location=location, tip='prijem', kolicina=2)
        self.order.lager_status = Order.LagerStatus.VALIDIRANO
        self.order.save(update_fields=['lager_status'])
        entry = LocationCleaningRequest.objects.create(order=self.order, order_number=self.order.broj,
            item_id_snapshot=self.item.pk, product=self.product, variation=variant,
            location=self.location, name='Crvena', needed=2, picked=0)
        resolve_request(entry.pk, clear=True, user=self.user)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, variation=variant, location=self.location).kolicina, 0)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, variation=other, location=self.location).kolicina, 2)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, variation=variant, location=elsewhere).kolicina, 2)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, variation=None, location=self.location).kolicina, 10)

    def test_short_pick_does_not_require_another_location_or_request_cleaning(self):
        from .models import LocationCleaningRequest
        other = WarehouseLocation.objects.create(sifra='PICK-B', naziv='Druga polica')
        apply_movement(product=self.product, location=other, tip='prijem', kolicina=5)
        confirm_short_pick(self.order, item_id=self.item.pk, loc=self.line['loc'], got=1, user=self.user)
        queue = _order_pick_bundle(self.order)[0]
        self.assertTrue(all(self.order.pick_state.get(row['key'], {}).get('done') for row in queue))
        self.assertFalse(LocationCleaningRequest.objects.filter(order=self.order).exists())
        apply_order_pick(self.order, [dict(row, got=1, done=True) for row in queue], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        self.assertEqual(self.stock(), 9)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=other).kolicina, 5)
