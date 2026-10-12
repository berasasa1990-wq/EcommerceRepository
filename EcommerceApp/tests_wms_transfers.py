from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .magacin import apply_movement
from .models import (
    Product, ProductVariation, WarehouseLocation, WarehouseMovement,
    WarehouseStock, WMSSettings, WMSTransfer,
)


class WMSTransferLifecycleTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser(
            'transfer-admin', 'transfer@example.test', 'test-only',
        ))
        self.source = WarehouseLocation.objects.create(sifra='TRANSFER-SRC', naziv='Izvor')
        self.destination = WarehouseLocation.objects.create(sifra='TRANSFER-DST', naziv='Odredište')
        self.product = Product.objects.create(naziv='Prenos test', cijena=10, stanje=0)
        apply_movement(product=self.product, location=self.source, tip='prijem', kolicina=8)
        self.url = reverse('staff_wms_section', args=['prenosnica'])
        self.permissions = patch('EcommerceApp.views_wms.module_locked', return_value=False)
        self.permissions.start()
        self.addCleanup(self.permissions.stop)

    def create_from_location(self, stock=None):
        stock = stock or WarehouseStock.objects.get(product=self.product, location=self.source)
        response = self.client.post(reverse('staff_wms_location', args=[self.source.pk]), {
            'stock_id': stock.pk, 'quantity': 3, 'destination': self.destination.pk,
        })
        self.assertEqual(response.status_code, 302)
        return WMSTransfer.objects.get()

    def assert_unmoved(self):
        stock = WarehouseStock.objects.get(product=self.product, location=self.source, variation=None)
        self.assertEqual((stock.kolicina, stock.rezervisano), (8, 0))
        self.assertFalse(WarehouseStock.objects.filter(location=self.destination, kolicina__gt=0).exists())
        self.assertFalse(WarehouseMovement.objects.filter(tip='transfer').exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 8)

    def test_create_and_cancel_keeps_stock_in_source(self):
        transfer = self.create_from_location()
        self.assert_unmoved()
        page = self.client.get(self.url)
        self.assertContains(page, 'Otkaži prenosnicu')
        self.assertContains(page, 'value="cancel"')
        response = self.client.post(self.url, {'transfer_id': transfer.pk, 'action': 'cancel'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(WMSTransfer.objects.filter(pk=transfer.pk).exists())
        self.assert_unmoved()
        self.assertEqual(self.client.get(self.url).context['wms_pending_transfers'], 0)
        # A stale completion form must not move a cancelled transfer.
        self.assertEqual(self.client.post(self.url, {'transfer_id': transfer.pk}).status_code, 404)
        self.assert_unmoved()

    def test_stock_page_creation_does_not_deduct_stock(self):
        WMSSettings.objects.create(pk=1, transfer_location=self.destination)
        stock = WarehouseStock.objects.get(product=self.product, location=self.source)
        response = self.client.post(reverse('staff_wms_section', args=['zalihe']), {
            'product_id': self.product.pk, 'stock_id': stock.pk,
            'source_location': self.source.pk, 'quantity': 3,
        })
        self.assertEqual(response.status_code, 302)
        transfer = WMSTransfer.objects.get()
        self.assert_unmoved()
        self.client.post(self.url, {'transfer_id': transfer.pk, 'action': 'cancel'})
        self.assert_unmoved()

    def test_completion_moves_once_and_cannot_be_cancelled(self):
        transfer = self.create_from_location()
        for data in ({'transfer_id': transfer.pk}, {'transfer_id': transfer.pk, 'action': 'complete'},
                     {'transfer_id': transfer.pk, 'action': 'cancel'}):
            self.assertEqual(self.client.post(self.url, data).status_code, 302)
        transfer.refresh_from_db()
        self.assertIsNotNone(transfer.completed_at)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=self.source).kolicina, 5)
        self.assertEqual(WarehouseStock.objects.get(product=self.product, location=self.destination).kolicina, 3)
        self.assertEqual(WarehouseMovement.objects.filter(tip='transfer').count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 8)
        self.assertNotContains(self.client.get(self.url, {'status': 'prenijete'}), 'value="cancel"')

    def test_cancel_variation_keeps_its_stock(self):
        variant = ProductVariation.objects.create(artikal=self.product, naziv='Varijanta', cijena=10)
        apply_movement(product=self.product, variation=variant, location=self.source, tip='prijem', kolicina=5)
        stock = WarehouseStock.objects.get(product=self.product, variation=variant, location=self.source)
        transfer = self.create_from_location(stock)
        self.client.post(self.url, {'transfer_id': transfer.pk, 'action': 'cancel'})
        stock.refresh_from_db()
        self.assertEqual((stock.kolicina, stock.rezervisano), (5, 0))
        self.assertFalse(WarehouseMovement.objects.filter(tip='transfer').exists())
        self.assertFalse(WarehouseStock.objects.filter(location=self.destination).exists())

    def test_unknown_action_does_not_complete_transfer(self):
        transfer = self.create_from_location()
        self.assertEqual(self.client.post(self.url, {'transfer_id': transfer.pk, 'action': 'typo'}).status_code, 400)
        transfer.refresh_from_db()
        self.assertIsNone(transfer.completed_at)
        self.assert_unmoved()

    def test_cancel_is_available_even_when_stock_module_locked(self):
        transfer = self.create_from_location()
        with patch('EcommerceApp.views_wms.module_locked', side_effect=lambda field: field == 'wms_zalihe'):
            self.assertContains(self.client.get(self.url), 'value="cancel"')
            self.assertEqual(self.client.post(self.url, {'transfer_id': transfer.pk, 'action': 'cancel'}).status_code, 302)
        self.assert_unmoved()
