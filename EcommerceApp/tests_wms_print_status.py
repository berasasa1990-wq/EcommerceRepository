from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import WMSOrder, Order, SystemDataRevision

class WMSPrintStatusTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('print-owner', 'print@example.test', 'test-password')
        self.client.force_login(self.user)
        self.source = Order.objects.create(broj='PRINT-WEB', ukupno=12)
        self.web = WMSOrder.objects.create(source_order=self.source, kupac='Web', tip='online', status='pakovanje')
        self.manual = WMSOrder.objects.create(kupac='Ručna', tip='online', status='pakovanje')
        self.sync = patch('EcommerceApp.wms_orders.sync_panel_orders')
        self.sync.start();self.addCleanup(self.sync.stop)

    def test_confirm_persists_for_manual_and_source_orders_and_highlights_selection(self):
        url = reverse('staff_wms_print_acknowledge', args=['racun'])
        response = self.client.post(url, {'orders':[self.web.pk, self.manual.pk]})
        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db();self.assertTrue(self.source.odstampana)
        self.assertEqual(SystemDataRevision.objects.filter(operation='PRINT').count(), 2)
        self.manual.refresh_from_db();self.assertEqual(self.manual.status, 'pakovanje')
        response = self.client.get(reverse('staff_wms_print_orders', args=['racun']), {'scope':'picking'})
        self.assertContains(response, 'class="wms-invoice-printed"', count=2)
        self.assertContains(response, 'class="wms-printed-badge"', count=2)

    def test_opening_print_page_waits_for_afterprint_and_has_no_confirmation_button(self):
        response = self.client.get(reverse('staff_wms_print_orders', args=['racun']), {'scope':'picking', 'stage':'print', 'orders':[self.manual.pk]})
        self.assertNotContains(response, 'Potvrdi odštampano')
        self.assertContains(response, "window.addEventListener('afterprint'")
        self.assertFalse(SystemDataRevision.objects.filter(operation='PRINT').exists())
        self.assertEqual(self.client.get(reverse('staff_wms_print_acknowledge', args=['racun'])).status_code, 405)

    def test_quantities_confirmation_does_not_mark_invoice_and_invalid_orders_are_atomic(self):
        self.client.post(reverse('staff_wms_print_acknowledge', args=['faktura']), {'orders':[self.web.pk]})
        self.source.refresh_from_db();self.assertFalse(self.source.odstampana)
        response = self.client.post(reverse('staff_wms_print_acknowledge', args=['racun']), {'orders':[self.manual.pk,999999]})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SystemDataRevision.objects.filter(operation='PRINT', after__kind='racun').exists())
        self.client.logout()
        self.assertEqual(self.client.post(reverse('staff_wms_print_acknowledge', args=['racun']), {'orders':[self.web.pk]}).status_code, 302)
