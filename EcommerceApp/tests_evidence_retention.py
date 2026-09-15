from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models.deletion import ProtectedError
from django.test import TestCase, RequestFactory
from django.utils import timezone

from .models import BarcodeConflict, Product, StaffSiteEvent, WarehouseLocation, WarehouseMovement
from .staff_alerts import cleanup_staff_events, push_staff_event
from .views_magacin import magacin_dupli_barkod_obrisi


class EvidenceRetentionTests(TestCase):
    def test_old_events_survive_cleanup_and_new_events(self):
        event = StaffSiteEvent.objects.create(tip='purchase', naslov='Stara kupovina')
        StaffSiteEvent.objects.filter(pk=event.pk).update(kreirano=timezone.now() - timedelta(days=365))
        self.assertEqual(cleanup_staff_events(), 0)
        push_staff_event('purchase', naslov='Nova kupovina')
        self.assertTrue(StaffSiteEvent.objects.filter(pk=event.pk).exists())

    def test_product_deletion_cannot_cascade_into_movement_history(self):
        product = Product.objects.create(naziv='Artikal', cijena=10)
        location = WarehouseLocation.objects.create(sifra='RET', naziv='Evidencija')
        movement = WarehouseMovement.objects.create(product=product, location=location, tip='prijem', kolicina=1)
        with self.assertRaises(ProtectedError):
            Product.objects.filter(pk=product.pk).delete()
        self.assertTrue(WarehouseMovement.objects.filter(pk=movement.pk).exists())

    def test_staff_cannot_delete_barcode_evidence(self):
        user = get_user_model().objects.create_user('retention-worker', is_staff=True)
        record = BarcodeConflict.objects.create(key='retention', barcode='123', product_name='A', other_name='B')
        request = RequestFactory().post('/')
        request.user = user
        # Invoke the view body independently of the warehouse role gate.
        view = magacin_dupli_barkod_obrisi
        while hasattr(view, '__wrapped__'):
            view = view.__wrapped__
        self.assertEqual(view(request, record.pk).status_code, 403)
        self.assertTrue(BarcodeConflict.objects.filter(pk=record.pk).exists())
