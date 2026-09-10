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

    def test_positive_partial_quantity_never_clears_location(self):
        confirm_short_pick(self.order, item_id=self.item.pk, loc=self.location.sifra,
                           got=1, clear_location=True, user=self.user)
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

    def test_zero_with_explicit_confirmation_clears_location(self):
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[self.order.broj]), {
            'action': 'pick_short', 'item_id': self.item.pk, 'loc': self.location.sifra,
            'got': '0', 'clear_location': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.stock(), 0)

    def test_posted_location_stock_cannot_exceed_order_quantity(self):
        apply_order_pick(self.order, [dict(self.line, got=10, need=10, done=True)], finalize=True, user=self.user)
        validate_order_stock(self.order, user=self.user)
        self.assertEqual(self.stock(), 7)
