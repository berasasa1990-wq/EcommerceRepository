from django.test import TestCase
from django.utils import timezone
from .models import WMSOrder, WMSPickLine
from .views_wms import pending_shortages


class PendingShortagesTests(TestCase):
    def test_all_confirmed_shortages_including_zero_and_unfinished_orders(self):
        now = timezone.now()
        included = []
        for status in ('nova', 'pakovanje', 'zapakovana'):
            order = WMSOrder.objects.create(kupac='Kupac', tip='online', status=status)
            for picked in (0, 2):
                included.append(WMSPickLine.objects.create(order=order, quantity=3, confirmed_quantity=picked, confirmed_at=now).pk)
            WMSPickLine.objects.create(order=order, quantity=3, confirmed_quantity=3, confirmed_at=now)
            WMSPickLine.objects.create(order=order, quantity=3, confirmed_quantity=0)
            for decision in ('clear', 'keep'):
                WMSPickLine.objects.create(order=order, quantity=3, confirmed_quantity=0, confirmed_at=now, shortage_resolution=decision, shortage_resolved_at=now)
        self.assertEqual(set(pending_shortages().values_list('pk', flat=True)), set(included))


class ResolveShortageTests(TestCase):
    def setUp(self):
        from unittest.mock import patch
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from .models import Product, WarehouseLocation, WarehouseStock, WMSOrderItem
        self.client.force_login(get_user_model().objects.create_superuser('shortage-admin', 'shortage@example.test', 'test-only'))
        p = patch('EcommerceApp.views_wms.module_locked', return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.product = Product.objects.create(naziv='Nedostaje', cijena=10, stanje=5)
        self.location = WarehouseLocation.objects.create(sifra='SHORT-A', naziv='Lokacija A')
        self.stock = WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=5)
        self.order = WMSOrder.objects.create(kupac='Kupac', tip='online', status='pakovanje')
        self.item = WMSOrderItem.objects.create(order=self.order, artikal=self.product, naziv='Nedostaje', cijena=10, kolicina=3)
        self.line = WMSPickLine.objects.create(order=self.order, wms_item=self.item, location=self.location, quantity=3, confirmed_quantity=0, confirmed_at=timezone.now())
        self.url = reverse('staff_wms_resolve_shortage', args=[self.line.pk])

    def test_reserved_stock_returns_message_instead_of_500(self):
        self.stock.rezervisano = 2
        self.stock.save(update_fields=['rezervisano'])
        response = self.client.post(self.url, {'decision': 'clear'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rezervisanom količinom')
        self.stock.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual((self.stock.kolicina, self.stock.rezervisano), (5, 2))
        self.assertIsNone(self.line.shortage_resolved_at)

    def test_clear_updates_stock_and_repeat_does_not_add_movement(self):
        from .models import WarehouseMovement
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.post(self.url, {'decision': 'clear'}).status_code, 302)
        locked_selection = [q['sql'] for q in queries if q['sql'].startswith('SELECT')
                            and '"shortage_resolved_at" IS NULL' in q['sql']
                            and '"confirmed_quantity" <' in q['sql']]
        self.assertEqual(len(locked_selection), 1)
        self.assertNotIn(' JOIN ', locked_selection[0])
        self.stock.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 0)
        self.assertEqual(self.line.shortage_resolution, 'clear')
        count = WarehouseMovement.objects.count()
        self.client.post(self.url, {'decision': 'clear'})
        self.assertEqual(WarehouseMovement.objects.count(), count)

    def test_missing_location_returns_message_without_resolving(self):
        self.line.location = None
        self.line.save(update_fields=['location'])
        self.assertEqual(self.client.post(self.url, {'decision': 'clear'}).status_code, 302)
        self.line.refresh_from_db()
        self.assertIsNone(self.line.shortage_resolved_at)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 5)

    def test_keep_preserves_stock(self):
        self.assertEqual(self.client.post(self.url, {'decision': 'keep'}).status_code, 302)
        self.stock.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 5)
        self.assertEqual(self.line.shortage_resolution, 'keep')

    def test_other_variant_stays_unresolved(self):
        from .models import ProductVariation, WMSOrderItem, WarehouseStock
        variant = ProductVariation.objects.create(artikal=self.product, naziv='Druga varijanta', cijena=10)
        item = WMSOrderItem.objects.create(order=self.order, artikal=self.product, varijacija=variant, naziv='Druga varijanta', cijena=10, kolicina=3)
        other = WMSPickLine.objects.create(order=self.order, wms_item=item, location=self.location, quantity=3, confirmed_quantity=1, confirmed_at=timezone.now())
        stock = WarehouseStock.objects.create(product=self.product, variation=variant, location=self.location, kolicina=4)
        self.client.post(self.url, {'decision': 'clear'})
        other.refresh_from_db()
        stock.refresh_from_db()
        self.assertIsNone(other.shortage_resolved_at)
        self.assertEqual(stock.kolicina, 4)

    def test_webshop_shortage_uses_same_cleanup(self):
        from .models import Order, OrderItem
        from django.urls import reverse
        source = Order.objects.create(ime_prezime='Web kupac', ukupno=30)
        item = OrderItem.objects.create(narudzba=source, artikal=self.product, naziv='Nedostaje', cijena=10, kolicina=3)
        order = WMSOrder.objects.create(kupac='Web kupac', tip='online', source_order=source)
        line = WMSPickLine.objects.create(order=order, item=item, location=self.location, quantity=3, confirmed_at=timezone.now())
        response = self.client.post(reverse('staff_wms_resolve_shortage', args=[line.pk]), {'decision': 'clear'})
        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.shortage_resolution, 'clear')
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 0)

    def test_missing_item_returns_message_without_server_error(self):
        self.line.wms_item = None
        self.line.save(update_fields=['wms_item'])
        response = self.client.post(self.url, {'decision': 'clear'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Artikal više nije dostupan.')
