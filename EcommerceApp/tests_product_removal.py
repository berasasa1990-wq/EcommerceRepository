from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from .models import Product, WarehouseLocation, WarehouseMovement, Order, OrderItem
from .magacin import obrisi_artikal_iz_baze, magacin_products_qs, MagacinError


class SuperuserProductRemovalTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('removaladmin', 'removal@example.com', 'test')
        self.product = Product.objects.create(naziv='Removal article', cijena=10, magacin_sync_at=timezone.now())

    def test_superuser_can_delete_unprotected_product(self):
        pk = self.product.pk
        self.assertEqual(obrisi_artikal_iz_baze(self.product, user=self.admin), 'deleted')
        self.assertFalse(Product.objects.filter(pk=pk).exists())

    def test_regular_staff_cannot_delete(self):
        staff = get_user_model().objects.create_user('staffremoval', is_staff=True)
        with self.assertRaisesMessage(MagacinError, 'Samo superuser'):
            obrisi_artikal_iz_baze(self.product, user=staff)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_movement_and_order_are_preserved_when_product_is_removed(self):
        location = WarehouseLocation.objects.create(sifra='REMOVE-A', naziv='Removal shelf')
        movement = WarehouseMovement.objects.create(product=self.product, location=location, tip=WarehouseMovement.Tip.KOREKCIJA, kolicina=1)
        order = Order.objects.create(ime_prezime='Removal history', ukupno=10)
        item = OrderItem.objects.create(narudzba=order, artikal=self.product, naziv=self.product.naziv, cijena=10, kolicina=1)
        self.assertEqual(obrisi_artikal_iz_baze(self.product, user=self.admin), 'archived')
        self.product.refresh_from_db()
        self.assertIsNotNone(self.product.obrisan_at)
        self.assertFalse(self.product.aktivan)
        self.assertFalse(magacin_products_qs().filter(pk=self.product.pk).exists())
        self.assertTrue(WarehouseMovement.objects.filter(pk=movement.pk).exists())
        item.refresh_from_db()
        self.assertEqual(item.artikal_id, self.product.pk)
