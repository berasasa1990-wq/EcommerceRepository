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
