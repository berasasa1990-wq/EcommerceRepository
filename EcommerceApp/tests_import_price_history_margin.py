from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase
from .views_magacin import _uvoz_marza_pct


class ImportPriceHistoryMarginTests(SimpleTestCase):
    def test_margin_uses_vpc_and_purchase_instead_of_saved_percentage(self):
        row = SimpleNamespace(vpc_netto=Decimal('15'), nabavna=Decimal('10'), vpc_marza_pct=Decimal('150'), mpc_brutto=Decimal('30'))
        self.assertEqual(_uvoz_marza_pct(row), Decimal('50.00'))

    def test_zero_negative_and_missing_values(self):
        for vpc, cost, expected in [('0', '10', '-100.00'), ('8', '10', '-20.00'), ('10', '10', '0.00'), ('10', '0', None), (None, '10', None), ('10', None, None)]:
            row = SimpleNamespace(vpc_netto=Decimal(vpc) if vpc is not None else None, nabavna=Decimal(cost) if cost is not None else None)
            self.assertEqual(_uvoz_marza_pct(row), Decimal(expected) if expected is not None else None)

    def test_mpc_netto_margin_excludes_vat_and_uses_fallback_price(self):
        from .views_magacin import _uvoz_mpc_netto_pct
        row = SimpleNamespace(mpc_brutto=Decimal('17.55'), nabavna=Decimal('10'))
        self.assertEqual(_uvoz_mpc_netto_pct(row), Decimal('50.00'))
        row.mpc_brutto = None
        self.assertEqual(_uvoz_mpc_netto_pct(row, Decimal('23.40')), Decimal('100.00'))
        self.assertIsNone(_uvoz_mpc_netto_pct(row))
        row.nabavna = Decimal('0')
        self.assertIsNone(_uvoz_mpc_netto_pct(row, Decimal('23.40')))
