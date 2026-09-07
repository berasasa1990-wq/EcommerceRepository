from django.test import TestCase, override_settings
from django.urls import reverse
from . import tests_ledger as helpers
from .models import WarehouseLocation, WarehouseStock


@override_settings(ALLOWED_HOSTS=['testserver'])
class LocationActionTests(TestCase):
    setUp = helpers.WarehouseLedgerTests.setUp

    def post_action(self, action, qty, destination=None):
        self.client.force_login(self.user)
        return self.client.post(reverse('staff_magacin_lokacije'), {
            'action': action, 'location_id': self.location.pk,
            'product_id': self.product.pk, 'kolicina': qty,
            'to_location_id': destination.pk if destination else '',
        })

    def test_move_then_remove(self):
        destination = WarehouseLocation.objects.create(sifra='MOVE-B', naziv='MOVE-B')
        response = self.post_action('premjesti', 3, destination)
        self.assertEqual(response.status_code, 302)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 7)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=destination).kolicina, 3)
        self.post_action('skini', 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 5)

    def test_reserved_stock_and_same_destination_are_protected(self):
        destination = WarehouseLocation.objects.create(sifra='MOVE-C', naziv='MOVE-C')
        self.stock.rezervisano = 9
        self.stock.save()
        for action in ('premjesti', 'skini'):
            self.post_action(action, 2, destination)
            self.stock.refresh_from_db()
            self.assertEqual((self.stock.kolicina, self.stock.rezervisano), (10, 9))
        self.post_action('premjesti', 1, self.location)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        self.assertFalse(WarehouseStock.objects.filter(location=destination).exists())
