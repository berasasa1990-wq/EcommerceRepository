from uuid import uuid4
from django.test import TestCase
from .models import B2BAccount, B2BSubmission, Order, WMSOrder
from .wms_orders import sync_panel_orders


class WMSOrderSourcesTests(TestCase):
    def setUp(self):
        self.web = Order.objects.create(ime_prezime='Web kupac', ukupno=25)
        self.manual = Order.objects.create(ime_prezime='Ručni kupac', ukupno=40, izvor=Order.Izvor.MAGACIN)
        self.b2b = Order.objects.create(ime_prezime='Firma', ukupno=117, izvor=Order.Izvor.MAGACIN)
        self.account = B2BAccount.objects.create(username='vp', company='Firma', telefon='065123456', adresa='Ulica 1', grad='Trebinje', email='vp@example.com')
        B2BSubmission.objects.create(account=self.account, order=self.b2b, token=uuid4(), payment='ziralno', netto_total=100)

    def test_all_sources_import_once_with_b2b_contact_and_labels(self):
        sync_panel_orders()
        sync_panel_orders()
        self.assertEqual(set(WMSOrder.objects.values_list('source_order_id', flat=True)), {self.web.pk, self.manual.pk, self.b2b.pk})
        self.assertEqual(WMSOrder.objects.count(), 3)
        wms = WMSOrder.objects.get(source_order=self.b2b)
        self.assertEqual(wms.tip, 'vp')
        self.assertEqual(wms.email, self.account.email)
        self.assertEqual(wms.telefon, self.account.telefon)
        self.assertEqual(wms.grad, self.account.grad)
        self.assertEqual(wms.order_source_label, 'B2B veleprodaja')
        self.assertEqual(wms.payment_label, 'Žiralno')
        self.assertEqual(WMSOrder.objects.get(source_order=self.manual).order_source_label, 'Ručni unos (Magacin)')

    def test_closed_pending_and_transfer_orders_are_not_imported(self):
        for source in Order.Izvor.values:
            for status in [Order.Status.CEKA_PLACANJE, Order.Status.ZAVRSENA, Order.Status.OTKAZANA]:
                Order.objects.create(ime_prezime='Isključena', ukupno=10, izvor=source, status=status)
            Order.objects.create(ime_prezime='Prenos u MP', ukupno=10, izvor=source)
            Order.objects.create(ime_prezime='Prenos', ukupno=10, izvor=source, pick_state={'kind':'prenos_mp'})
        sync_panel_orders()
        self.assertEqual(WMSOrder.objects.count(), 3)

    def test_existing_picking_state_is_preserved(self):
        existing = WMSOrder.objects.create(source_order=self.b2b, kupac='Firma', tip='vp', status='pakovanje')
        sync_panel_orders()
        existing.refresh_from_db()
        self.assertEqual(existing.status, 'pakovanje')
        self.assertEqual(WMSOrder.objects.filter(source_order=self.b2b).count(), 1)
