from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Product, WarehouseLocation, WarehouseMovement, Order
from .magacin import apply_movement
from .warehouse_history import attach_history, highlight_pick_qty_note


@override_settings(ALLOWED_HOSTS=['testserver'], SITE_PREP_ENABLED=False,
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class StockHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('history', 'h@example.com', 'secret')
        self.product = Product.objects.create(naziv='Istorija artikla', cijena=Decimal('10'), magacin_sync_at=timezone.now())
        self.source = WarehouseLocation.objects.create(sifra='H-A', naziv='Izvorna polica')
        self.dest = WarehouseLocation.objects.create(sifra='H-B', naziv='Odredišna polica')
        self.order = Order.objects.create(ime_prezime='Firma kupac', telefon='061123456', ukupno=Decimal('30'))
        self.client.force_login(self.user)

    def test_recent_and_full_history_exclude_reservations_and_zero_changes(self):
        receipts = [WarehouseMovement.objects.create(product=self.product, location=self.source,
                    tip='prijem', kolicina=i + 1) for i in range(12)]
        for _ in range(12):
            WarehouseMovement.objects.create(product=self.product, location=self.source, tip='rezervacija', kolicina=4)
        WarehouseMovement.objects.create(product=self.product, location=self.source, tip='sync', kolicina=0)
        recent = self.client.get(reverse('staff_magacin_artikal', args=[self.product.pk]))
        self.assertEqual([m.pk for m in recent.context['movements']], [m.pk for m in reversed(receipts[-10:])])
        full = self.client.get(reverse('staff_magacin_istorija', args=[self.product.pk]))
        self.assertEqual(full.context['page'].paginator.count, 12)
        self.assertContains(full, 'Dodavanje u lokaciju')
        self.assertContains(full, 'H-A')
        self.assertNotContains(full, 'H-A — Izvorna polica')
        self.assertContains(full, '+12')

    def test_sale_captures_customer_order_and_location_at_movement_time(self):
        apply_movement(product=self.product, location=self.source, tip='prijem', kolicina=10)
        movement = apply_movement(product=self.product, location=self.source, tip='prodaja', kolicina=3,
                                  order=self.order, user=self.user, napomena='Prodaja kupcu')
        self.assertEqual(movement.order_id, self.order.pk)
        self.order.ime_prezime = 'Promijenjen kupac'
        self.order.save()
        self.source.naziv = 'Promijenjena polica'
        self.source.save()
        page = self.client.get(reverse('staff_magacin_istorija', args=[self.product.pk]))
        self.assertContains(page, 'Firma kupac')
        self.assertContains(page, 'H-A')
        self.assertNotContains(page, 'H-A — Izvorna polica')
        self.assertContains(page, reverse('staff_order_detail', args=[self.order.broj]))
        self.assertContains(page, '−3')

    def test_transfers_show_both_locations_and_legacy_order_reference(self):
        transfer = WarehouseMovement.objects.create(product=self.product, location=self.source, to_location=self.dest,
                      tip='transfer', kolicina=2, napomena=f'Prenos u MP #{self.order.broj}')
        plain = WarehouseMovement.objects.create(product=self.product, location=self.source, to_location=self.dest,
                      tip='transfer', kolicina=1)
        rows = attach_history([transfer, plain])
        self.assertEqual(rows[0].history_type, 'Prenos u MP')
        self.assertEqual(rows[1].history_type, 'Transfer iz lokacije u lokaciju')
        self.assertEqual(str(rows[1].history_note), 'Prenos iz lokacije u lokaciju')
        self.assertEqual(str(rows[0].history_note), f'Prenos u MP #{self.order.broj}')
        generic = WarehouseMovement.objects.create(
            product=self.product, location=self.source, to_location=self.dest,
            tip='transfer', kolicina=1, napomena='Transfer',
        )
        custom = WarehouseMovement.objects.create(
            product=self.product, location=self.source, to_location=self.dest,
            tip='transfer', kolicina=1, napomena='Premještaj u vanjski lager',
        )
        extra = attach_history([generic, custom])
        self.assertEqual(str(extra[0].history_note), 'Prenos iz lokacije u lokaciju')
        self.assertEqual(str(extra[1].history_note), 'Premještaj u vanjski lager')
        self.assertEqual(rows[0].history_source, 'H-A — Izvorna polica')
        self.assertEqual(rows[0].history_destination, 'H-B — Odredišna polica')
        self.assertEqual(rows[0].history_quantity, '−2 → +2')
        WarehouseMovement.objects.filter(pk=transfer.pk).update(order=None, order_number='', customer_name='')
        transfer.refresh_from_db()
        self.assertEqual(attach_history([transfer])[0].history_customer, 'Firma kupac')

    def test_pick_qty_phrases_are_marked_red_in_history_note(self):
        partial = highlight_pick_qty_note(
            'Stanje 2 → pokupljeno djelimično 1 → očišćena lok. stanje 0')
        full = highlight_pick_qty_note('Stanje 2 → pokupljeno 2 → stanje 0')
        self.assertIn('<strong class="mg-hist-picked">pokupljeno djelimično 1</strong>', str(partial))
        self.assertIn('<strong class="mg-hist-picked">pokupljeno 2</strong>', str(full))
        self.assertNotIn('mg-hist-picked">Stanje', str(full))
        move = WarehouseMovement.objects.create(
            product=self.product, location=self.source, tip='korekcija', kolicina=-1,
            napomena='Stanje 2 → pokupljeno djelimično 1 → očišćena lok. stanje 0',
        )
        self.assertIn(
            'mg-hist-picked">pokupljeno djelimično 1',
            str(attach_history([move])[0].history_note),
        )
        missing = highlight_pick_qty_note(
            'Stanje 2 → nije pronađeno → očišćena lok. stanje 0')
        added = highlight_pick_qty_note('Dodano na lokaciju')
        inserted = highlight_pick_qty_note('Ubaci u lokaciju')
        self.assertIn('<strong class="mg-hist-missing">nije pronađeno</strong>', str(missing))
        self.assertIn('<strong class="mg-hist-added">Dodano na lokaciju</strong>', str(added))
        self.assertIn('<strong class="mg-hist-added">Ubaci u lokaciju</strong>', str(inserted))
        receipt = WarehouseMovement.objects.create(
            product=self.product, location=self.source, tip='prijem', kolicina=4,
            napomena='Dodano na lokaciju',
        )
        attached = attach_history([receipt])[0]
        self.assertEqual(attached.history_type, 'Dodavanje u lokaciju')
        self.assertFalse(getattr(attached, 'history_type_class', ''))
        self.assertIn('mg-hist-added">Dodano na lokaciju', str(attached.history_note))
        page = self.client.get(reverse('staff_magacin_istorija', args=[self.product.pk]))
        self.assertContains(page, 'Dodavanje u lokaciju')
        self.assertNotContains(page, '<strong class="mg-hist-added">Dodavanje u lokaciju</strong>')
        self.assertContains(page, '<strong class="mg-hist-added">Dodano na lokaciju</strong>', html=True)
