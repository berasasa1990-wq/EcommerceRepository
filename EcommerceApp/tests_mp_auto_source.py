from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .magacin import apply_movement
from .models import Product, WarehouseLocation, WarehouseStock, Order


@override_settings(SECURE_SSL_REDIRECT=False, SITE_PREP_ENABLED=False)
class MpAutoSourceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('mp-auto', 'mp@example.com', 'pass')
        self.client.force_login(self.user)
        self.product = Product.objects.create(naziv='Auto MP', sifra='AUTO-MP', cijena=10,
            stanje=3, na_stanju=True, magacin_sync_at=timezone.now())
        self.reserved = WarehouseLocation.objects.create(sifra='Magacin3', naziv='Magacin3')
        self.free = WarehouseLocation.objects.create(sifra='Vrata-1', naziv='Vrata-1')
        self.mp = WarehouseLocation.objects.create(sifra='MALOPRODAJA', naziv='Maloprodaja')
        for location in (self.reserved, self.free, self.mp):
            apply_movement(product=self.product, location=location, tip='prijem', kolicina=1)
        WarehouseStock.objects.filter(product=self.product, location=self.reserved).update(rezervisano=1)

    def transfer(self, qty=1):
        return self.client.post(reverse('staff_magacin_artikal', args=[self.product.pk]), {
            'action': 'kretanje', 'mode': 'mp', 'kolicina': qty,
            'location_id': self.reserved.pk,
        })

    def test_skips_reserved_location_and_reserves_free_warehouse_stock(self):
        self.assertEqual(self.transfer().status_code, 302)
        order = Order.objects.get(ime_prezime='Prenos u MP')
        self.assertEqual(order.stavke.get().kolicina, 1)
        stock = WarehouseStock.objects.get(product=self.product, location=self.free)
        self.assertEqual(stock.rezervisano, 1)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=self.mp).rezervisano, 0)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=self.reserved).rezervisano, 1)

    def test_never_uses_mp_or_reserved_stock_to_satisfy_quantity(self):
        self.assertEqual(self.transfer(qty=2).status_code, 302)
        self.assertFalse(Order.objects.filter(ime_prezime='Prenos u MP').exists())
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=self.free).rezervisano, 0)
