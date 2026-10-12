from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from .models import Order, OrderItem, Product, WarehouseLocation, WMSOrder, WMSPickLine
from .wms_legacy import complete_legacy_orders


class WMSCompletionConfirmationTests(TestCase):
    def test_partial_and_zero_confirmation_are_carried_to_source_order(self):
        for quantity in (0, 2):
            with self.subTest(quantity=quantity):
                product = Product.objects.create(naziv='Mamac', cijena=10)
                source = Order.objects.create(ime_prezime='Kupac', ukupno=30)
                item = OrderItem.objects.create(narudzba=source, artikal=product, naziv='Mamac', cijena=10, kolicina=3)
                location = WarehouseLocation.objects.create(sifra=f'A-{quantity}', naziv='Polica')
                wms = WMSOrder.objects.create(source_order=source, kupac='Kupac', tip='vp', status='zapakovana')
                WMSPickLine.objects.create(order=wms, item=item, location=location, quantity=3, confirmed_quantity=quantity, confirmed_at=timezone.now())
                def validate(order, user=None):
                    order.refresh_from_db()
                    rows = list(order.pick_state.values())
                    self.assertEqual(len(rows), 1)
                    self.assertTrue(rows[0]['done'])
                    self.assertEqual(rows[0]['got'], quantity)
                with patch('EcommerceApp.magacin.validate_order_stock', side_effect=validate):
                    complete_legacy_orders(None, [wms.pk])

    def test_refresh_return_and_start_keep_existing_picking_progress(self):
        from django.contrib.auth import get_user_model
        from django.http import HttpResponse
        user = get_user_model().objects.create_superuser('persistent-pick', 'persist@example.com', 'test-only')
        self.client.force_login(user)
        order = WMSOrder.objects.create(kupac='Kupac', tip='online', status='pakovanje')
        product = Product.objects.create(naziv='Mamac', cijena=10)
        from .models import WMSOrderItem
        item = WMSOrderItem.objects.create(order=order, artikal=product, naziv='Mamac', cijena=10, kolicina=3)
        location = WarehouseLocation.objects.create(sifra='PERSIST', naziv='Polica')
        confirmed = WMSPickLine.objects.create(order=order, wms_item=item, location=location, quantity=2, confirmed_quantity=1, confirmed_at=timezone.now())
        pending = WMSPickLine.objects.create(order=order, wms_item=item, location=location, quantity=1)
        path = f'/wms/narudzbe/{order.pk}/odvajanje/'
        def capture(request, template, context):
            self.assertEqual(context['completed'], 1)
            self.assertEqual(context['next_line'].pk, pending.pk)
            if request.GET.get('resume'):
                self.assertEqual(context['current'].pk, pending.pk)
            return HttpResponse('OK')
        with patch('EcommerceApp.views_wms.module_locked', return_value=False), patch('EcommerceApp.views_wms.render', side_effect=capture):
            for _ in range(2):
                self.assertEqual(self.client.get(path).status_code, 200)
            self.assertEqual(self.client.get(path, {'resume': '1'}).status_code, 200)
            response = self.client.post(path, {'action': 'start'})
            self.assertEqual(response.status_code, 302)
            self.assertIn(f'line={pending.pk}', response.url)
        confirmed.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(confirmed.confirmed_quantity, 1)
        self.assertIsNotNone(confirmed.confirmed_at)
        self.assertEqual(order.status, 'pakovanje')
        self.assertEqual(order.pick_lines.count(), 2)
