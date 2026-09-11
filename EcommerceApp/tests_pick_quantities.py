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

    def test_partial_pick_clears_location_and_offers_other_stock(self):
        a04 = WarehouseLocation.objects.create(sifra='A-04', naziv='Polica A04')
        mp = WarehouseLocation.objects.create(sifra='Maloprodaja', naziv='Maloprodaja')
        product = Product.objects.create(
            naziv='Manje pa MP', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a04, tip='prijem', kolicina=2)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=2)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'Manje pa MP', 'telefon': '061444444',
            'product_id': [product.pk], 'variation_id': [''], 'kolicina': ['2'], 'mp_ok': ['0']})
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='Manje pa MP')
        item = order.stavke.get()
        self.assertEqual([row['loc'] for row in _order_pick_bundle(order)[0]], ['A-04'])
        denied = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-04',
            'got': '1', 'clear_location': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=a04).kolicina, 2)
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-04',
            'got': '1', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload.get('ok'), payload)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=a04).kolicina, 0)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=mp).kolicina, 2)
        remaining = [
            (row['loc'], row['need'], bool(row.get('is_mp')))
            for row in payload['queue'] if not row.get('already_picked')
        ]
        self.assertEqual(remaining, [('Maloprodaja', 1, True)])
        picked = [
            (row['loc'], row['need'])
            for row in payload['queue'] if row.get('already_picked')
        ]
        self.assertEqual(picked, [('A-04', 1)])
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 2)
        self.assertEqual(item.kolicina_pokupljeno, 1)

    def test_zero_without_confirmation_preserves_stock_even_on_finalize(self):
        confirm_short_pick(self.order, item_id=self.item.pk, loc=self.location.sifra, got=0, user=self.user)
        self.assertEqual(self.stock(), 10)
        apply_order_pick(self.order, [dict(self.line, got=0, done=True)], finalize=True, user=self.user)
        self.assertEqual(self.stock(), 10)

    def test_zero_with_explicit_confirmation_clears_location(self):
        denied = self.client.post(reverse('staff_magacin_pakuj_detail', args=[self.order.broj]), {
            'action': 'pick_short', 'item_id': self.item.pk, 'loc': self.location.sifra,
            'got': '0', 'clear_location': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.stock(), 10)
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[self.order.broj]), {
            'action': 'pick_short', 'item_id': self.item.pk, 'loc': self.location.sifra,
            'got': '0', 'clear_location': '1', 'lozinka': 'admin'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stock(), 0)

    def test_zero_on_maloprodaja_clears_physical_mp_location(self):
        from .magacin import clear_pick_location_stock
        mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        product = Product.objects.create(naziv='MP artikal', cijena=Decimal('5'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=mp, tip='prijem', kolicina=6)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'MP picking', 'telefon': '061999999',
            'product_id': [product.pk], 'variation_id': [''], 'kolicina': ['2'], 'mp_ok': ['0']})
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='MP picking')
        item = order.stavke.get()
        queue = _order_pick_bundle(order)[0]
        self.assertTrue(queue)
        loc = queue[0]['loc']
        self.assertTrue(loc in {'B-03', 'MP'} or 'maloprodaja' in (queue[0].get('loc_path') or '').casefold())
        denied = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': loc,
            'got': '0', 'clear_location': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=mp).kolicina, 6)
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': loc,
            'got': '0', 'clear_location': '1', 'lozinka': 'admin'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=mp).kolicina, 0)
        other = Product.objects.create(naziv='MP label', cijena=Decimal('4'), magacin_sync_at=timezone.now())
        apply_movement(product=other, location=mp, tip='prijem', kolicina=4)
        labeled = Order.objects.create(ime_prezime='MP label order', ukupno=Decimal('8.00'))
        labeled_item = labeled.stavke.create(artikal=other, naziv=other.naziv, kolicina=1, cijena=Decimal('4.00'))
        result = clear_pick_location_stock(labeled, labeled_item, loc='MP', user=self.user)
        self.assertEqual(result['cleared'], 4)
        self.assertEqual(WarehouseStock.objects.get(product=other, location=mp).kolicina, 0)

    def test_zero_on_warehouse_then_offers_maloprodaja(self):
        a04 = WarehouseLocation.objects.create(sifra='A-04', naziv='Polica A04')
        mp = WarehouseLocation.objects.create(sifra='B-03', naziv='Maloprodaja Sarajevo')
        product = Product.objects.create(
            naziv='A04 pa MP', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a04, tip='prijem', kolicina=2)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=2)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'A04 pa MP', 'telefon': '061111111',
            'product_id': [product.pk], 'variation_id': [''], 'kolicina': ['2'], 'mp_ok': ['0']})
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='A04 pa MP')
        item = order.stavke.get()
        queue = _order_pick_bundle(order)[0]
        self.assertEqual([(row['loc'], row['need']) for row in queue], [('A-04', 2)])
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-04',
            'got': '0', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload.get('ok'), payload)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=a04).kolicina, 0)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=mp).kolicina, 2)
        remaining = [row for row in payload['queue'] if not row.get('already_picked')]
        self.assertEqual([(row['loc'], row['need']) for row in remaining], [('B-03', 2)])
        self.assertTrue(remaining[0].get('is_mp'))
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 2)
        self.assertEqual(payload.get('shortages') or [], [])
        self.assertIn('B-03', payload.get('message') or '')

    def test_zero_on_a04_offers_location_named_maloprodaja(self):
        a04 = WarehouseLocation.objects.create(sifra='A-04', naziv='Polica A04')
        mp = WarehouseLocation.objects.create(sifra='Maloprodaja', naziv='Maloprodaja')
        product = Product.objects.create(
            naziv='A04 pa Maloprodaja', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a04, tip='prijem', kolicina=2)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=2)
        created = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': 'A04 pa Maloprodaja', 'telefon': '061222222',
            'product_id': [product.pk], 'variation_id': [''], 'kolicina': ['2'], 'mp_ok': ['0']})
        self.assertEqual(created.status_code, 302)
        order = Order.objects.get(ime_prezime='A04 pa Maloprodaja')
        item = order.stavke.get()
        self.assertEqual([row['loc'] for row in _order_pick_bundle(order)[0]], ['A-04'])
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-04',
            'got': '0', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        remaining = [row for row in payload['queue'] if not row.get('already_picked')]
        self.assertEqual([(row['loc'], row['need']) for row in remaining], [('Maloprodaja', 2)])
        self.assertTrue(remaining[0].get('is_mp'))
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 2)

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
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-04',
            'got': '0', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        remaining = [
            (row['loc'], row['need'], bool(row.get('is_mp')))
            for row in response.json()['queue'] if not row.get('already_picked')
        ]
        self.assertEqual(remaining, [('Z-01', 1, False), ('Maloprodaja', 2, True)])
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 3)

    def test_posted_location_stock_cannot_exceed_order_quantity(self):
        apply_order_pick(self.order, [dict(self.line, got=10, need=10, done=True)], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        self.assertEqual(self.stock(), 7)

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

    def test_partial_find_keeps_ordered_qty_and_searches_remainder(self):
        a01 = WarehouseLocation.objects.create(sifra='A-01', naziv='Polica A01')
        b04 = WarehouseLocation.objects.create(sifra='B-04', naziv='Polica B04')
        mp = WarehouseLocation.objects.create(sifra='Maloprodaja', naziv='Maloprodaja')
        product = Product.objects.create(
            naziv='Jedan od dva pa ostalo', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a01, tip='prijem', kolicina=2)
        apply_movement(product=product, location=b04, tip='prijem', kolicina=2)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=3)
        order = self._make_order('Jedan od dva pa ostalo', product, 5, '061555002')
        item = order.stavke.get()
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-01',
            'got': '1', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload.get('ok'), payload)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=a01).kolicina, 0)
        remaining = [
            (row['loc'], row['need'], bool(row.get('is_mp')))
            for row in payload['queue'] if not row.get('already_picked')
        ]
        self.assertEqual(remaining, [('B-04', 2, False), ('Maloprodaja', 2, True)])
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 5)
        self.assertEqual(item.kolicina_pokupljeno, 1)
        from .models import WarehouseMovement
        moves = list(WarehouseMovement.objects.filter(
            product=product, location=a01, order=order,
        ).exclude(tip='rezervacija'))
        self.assertEqual(len(moves), 1, [f'{m.tip}:{m.napomena}' for m in moves])
        self.assertEqual(
            moves[0].napomena,
            'Stanje 2 → pokupljeno djelimično 1 → očišćena lok. stanje 0',
        )

    def test_finish_with_shortage_matches_confirmed_physical_stock(self):
        a01 = WarehouseLocation.objects.create(sifra='A-01', naziv='Polica A01')
        b01 = WarehouseLocation.objects.create(sifra='B-01', naziv='Polica B01')
        mp = WarehouseLocation.objects.create(sifra='Maloprodaja', naziv='Maloprodaja')
        product = Product.objects.create(
            naziv='Tri od pet', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a01, tip='prijem', kolicina=2)
        apply_movement(product=product, location=b01, tip='prijem', kolicina=2)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=1)
        order = self._make_order('Tri od pet', product, 5, '061555003')
        item = order.stavke.get()
        self.assertEqual(
            [(row['loc'], row['need']) for row in _order_pick_bundle(order)[0]],
            [('A-01', 2), ('B-01', 2), ('Maloprodaja', 1)],
        )
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-01',
            'got': '0', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload.get('ok'), payload)
        remaining = [row for row in payload['queue'] if not row.get('already_picked')]
        self.assertEqual([(row['loc'], row['need']) for row in remaining], [('B-01', 2), ('Maloprodaja', 1)])
        item.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(item.kolicina, 5)
        self.assertEqual(item.kolicina_pokupljeno, 0)
        apply_order_pick(order, [dict(row, got=row['need'], done=True) for row in remaining], finalize=True, user=self.user)
        validate_order_stock(order, user=self.user)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=a01).kolicina, 0)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=b01).kolicina, 0)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=mp).kolicina, 0)
        item.refresh_from_db()
        self.assertEqual(item.kolicina, 3)
        self.assertEqual(item.kolicina_faktura, 3)
        from .models import WarehouseMovement
        empty = WarehouseMovement.objects.filter(
            product=product, location=a01, tip='korekcija', order=order,
        ).latest('pk')
        self.assertEqual(
            empty.napomena,
            'Stanje 2 → nije pronađeno → očišćena lok. stanje 0',
        )
        sales = list(
            WarehouseMovement.objects.filter(product=product, order=order, tip='prodaja')
            .values_list('napomena', flat=True)
        )
        self.assertIn('Stanje 2 → pokupljeno 2 → stanje 0', sales)
        self.assertIn('Stanje 1 → pokupljeno 1 → stanje 0', sales)

    def test_zero_on_only_location_keeps_order_for_partial_finish(self):
        a01 = WarehouseLocation.objects.create(sifra='A-01', naziv='Polica A01')
        product = Product.objects.create(
            naziv='Samo A01', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a01, tip='prijem', kolicina=2)
        order = self._make_order('Samo A01', product, 2, '061555004')
        item = order.stavke.get()
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A-01',
            'got': '0', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload.get('ok'), payload)
        self.assertEqual(WarehouseStock.objects.get(product=product, location=a01).kolicina, 0)
        remaining = [row for row in payload['queue'] if not row.get('already_picked')]
        self.assertEqual(remaining, [])
        item.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(item.kolicina, 2)
        self.assertNotEqual(order.status, Order.Status.OTKAZANA)
        self.assertIn('Poručeno: 2', payload.get('message') or '')
        self.assertIn('Pokupljeno: 0', payload.get('message') or '')
        self.assertIn('Nedostaje: 2', payload.get('message') or '')

    def test_packing_print_shows_qty_and_location_not_taken_label(self):
        from .views_magacin import _build_picked_packing_lines

        a10 = WarehouseLocation.objects.create(sifra='A10', naziv='Polica A10')
        mp = WarehouseLocation.objects.create(sifra='Maloprodaja', naziv='Maloprodaja')
        product = Product.objects.create(
            naziv='Packing taken', cijena=Decimal('8'), magacin_sync_at=timezone.now())
        apply_movement(product=product, location=a10, tip='prijem', kolicina=2)
        apply_movement(product=product, location=mp, tip='prijem', kolicina=2)
        order = self._make_order('Packing taken', product, 3, '061555010')
        item = order.stavke.get()
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {
            'action': 'pick_short', 'item_id': item.pk, 'loc': 'A10',
            'got': '1', 'clear_location': '1', 'lozinka': 'admin',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        remaining = [row for row in response.json()['queue'] if not row.get('already_picked')]
        order.refresh_from_db()
        apply_order_pick(order, [dict(row, got=row['need'], done=True) for row in remaining], finalize=True, user=self.user)
        lines, _ = _build_picked_packing_lines(order)
        self.assertTrue(lines)
        shown = [
            f"{pick['take']}× {pick['location_name']}"
            for pick in lines[0]['picks']
        ]
        joined = ' · '.join(shown)
        self.assertNotIn('taken', joined.casefold())
        self.assertIn('1× A10', shown)
        self.assertTrue(any(row.endswith('Maloprodaja') and row.startswith('2×') for row in shown), shown)
