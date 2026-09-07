import json
from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from django.test import TestCase, override_settings
from . import tests_ledger as ledger_test_helpers
from .models import Order, OrderItem
from .magacin import reserve_for_order


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class PickingPauseTests(TestCase):
    setUp = ledger_test_helpers.WarehouseLedgerTests.setUp
    def make_order(self):
        order = Order.objects.create(broj='PAUSE-1', ime_prezime='Kupac', ukupno=20,
            izvor=Order.Izvor.MAGACIN, lager_status=Order.LagerStatus.REZERVISANO)
        item = OrderItem.objects.create(narudzba=order, artikal=self.product, naziv='Artikal', cijena=10, kolicina=2)
        reserve_for_order(order, self.product, 2, user=self.user)
        self.client.force_login(self.user)
        return order, item

    def test_pause_preserves_progress_and_resume_after_long_time(self):
        order, item = self.make_order()
        key = f'{item.pk}:{self.location.sifra}'
        payload = [{'key': key, 'item_id': item.pk, 'loc': self.location.sifra, 'got': 1, 'need': 2, 'done': False}]
        url = reverse('staff_magacin_pakuj_detail', args=[order.broj])
        response = self.client.post(url, {'action': 'pick_pause', 'pick_json': json.dumps(payload)}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], reverse('staff_magacin_pakuj'))
        order.refresh_from_db()
        self.assertIsNone(order.pick_claimed_by_id)
        self.assertEqual(order.pick_state[key]['got'], 1)
        saved = order.pick_state
        Order.objects.filter(pk=order.pk).update(kreirana=timezone.now()-timedelta(days=120))
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.pick_state, saved)
        self.assertEqual(order.status, Order.Status.NOVA)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        self.assertEqual(self.stock.rezervisano, 2)
        self.assertContains(response, 'data-picking-list="1"', count=2)
        self.assertNotContains(response, 'id="pkReset"')

    def test_cancel_clears_active_pick_and_cancels_order(self):
        order, item = self.make_order()
        order.pick_state = {'draft': {'item_id': item.pk, 'got': 1}}
        order.save()
        response = self.client.post(reverse('staff_magacin_pakuj_detail', args=[order.broj]), {'action': 'otkazi'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.OTKAZANA)
        self.assertEqual(order.lager_status, Order.LagerStatus.OTKAZANO)
        self.assertEqual(order.pick_state, {})
        self.assertIsNone(order.pick_claimed_by_id)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 0)
