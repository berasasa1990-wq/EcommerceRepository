from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .views import _random_sale_order


class SaleRandomOrderTests(SimpleTestCase):
    @patch('EcommerceApp.views.timezone.localdate', return_value=date(2026, 11, 4))
    def test_random_mix_preserves_items_and_stays_stable_for_pagination(self, today):
        products = [SimpleNamespace(pk=i, cijena=i) for i in range(1, 21)]
        first = _random_sale_order(products)
        second = _random_sale_order(list(reversed(products)))
        self.assertEqual([p.pk for p in first], [p.pk for p in second])
        self.assertEqual({p.pk for p in first}, {p.pk for p in products})
        self.assertNotEqual([p.pk for p in first], [p.pk for p in products])
        self.assertEqual([p.pk for p in products], list(range(1, 21)))

    def test_mix_changes_on_another_day(self):
        products = [SimpleNamespace(pk=i) for i in range(1, 21)]
        with patch('EcommerceApp.views.timezone.localdate', return_value=date(2026, 11, 4)):
            first = _random_sale_order(products)
        with patch('EcommerceApp.views.timezone.localdate', return_value=date(2026, 11, 5)):
            second = _random_sale_order(products)
        self.assertNotEqual([p.pk for p in first], [p.pk for p in second])
