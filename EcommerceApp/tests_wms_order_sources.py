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

    def test_receipt_selection_and_direct_print_exclude_b2b(self):
        from unittest.mock import patch
        from django.contrib.auth import get_user_model
        from django.http import HttpResponse
        from django.utils import timezone
        self.client.force_login(get_user_model().objects.create_superuser('receipt-wms', 'receipt@example.com', 'test-only'))
        sync_panel_orders()
        WMSOrder.objects.update(status='pakovanje')
        expected = set(WMSOrder.objects.exclude(source_order=self.b2b).values_list('pk', flat=True))
        b2b_id = WMSOrder.objects.get(source_order=self.b2b).pk
        def capture(request, template, context):
            orders = context.get('orders')
            ids = [o.pk for o in orders] if orders is not None else [job['order'].pk for job in context['jobs']]
            return HttpResponse(','.join(map(str, ids)))
        with patch('EcommerceApp.views_wms.module_locked', return_value=False), patch('EcommerceApp.views_wms.render', side_effect=capture):
            response = self.client.get('/wms/narudzbe/stampa/racun/', {'date': timezone.localdate().isoformat()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(map(int, response.content.decode().split(','))), expected)
            response = self.client.get('/wms/narudzbe/stampa/racun/', {'stage': 'print', 'orders': [*expected, b2b_id]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(map(int, response.content.decode().split(','))), expected)

    def test_all_picking_receipts_ignore_date_and_preserve_scope_when_printing(self):
        from unittest.mock import patch
        from datetime import timedelta
        from django.contrib.auth import get_user_model
        from django.http import HttpResponse
        from django.utils import timezone
        self.client.force_login(get_user_model().objects.create_superuser('all-picking', 'all@example.com', 'test-only'))
        sync_panel_orders()
        WMSOrder.objects.update(status='pakovanje')
        Order.objects.filter(pk=self.manual.pk).update(kreirana=timezone.now()-timedelta(days=10))
        extra = WMSOrder.objects.create(kupac='Nova', tip='online', status='nova')
        ids = set(WMSOrder.objects.exclude(source_order=self.b2b).exclude(pk=extra.pk).values_list('pk', flat=True))
        def capture(request, template, context):
            orders = context.get('orders')
            rows = orders if orders is not None else [job['order'] for job in context['jobs']]
            return HttpResponse(','.join(str(order.pk) for order in rows))
        with patch('EcommerceApp.views_wms.module_locked', return_value=False), patch('EcommerceApp.views_wms.render', side_effect=capture):
            for stage in ('', 'print'):
                response = self.client.get('/wms/narudzbe/stampa/racun/', {'scope': 'picking', 'stage': stage, 'orders': list(WMSOrder.objects.values_list('pk', flat=True))})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(map(int, response.content.decode().split(','))), ids)
