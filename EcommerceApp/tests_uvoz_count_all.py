from decimal import Decimal
from django.test import TestCase
from .models import Uvoz, UvozStavka, WarehouseStock
from .magacin import mark_all_uvoz_counted, MagacinError


class CountWholeImportTests(TestCase):
    def test_all_expected_quantities_are_counted_without_receiving_stock(self):
        entry = Uvoz.objects.create(naziv='Count all', izvor='magacin', zaliha_primljena=False)
        line = UvozStavka.objects.create(uvoz=entry, artikal_naziv='First', kolicina='4.5', popisano=1)
        zero = UvozStavka.objects.create(uvoz=entry, artikal_naziv='Zero', kolicina=0)
        skipped = UvozStavka.objects.create(uvoz=entry, artikal_naziv='Skipped', kolicina=3, status='skipped')
        self.assertEqual(mark_all_uvoz_counted(entry), 2)
        line.refresh_from_db(); zero.refresh_from_db(); skipped.refresh_from_db(); entry.refresh_from_db()
        self.assertEqual(line.popisano, Decimal('4.5'))
        self.assertEqual(zero.popisano, Decimal('0'))
        self.assertIsNotNone(line.popisano_at)
        self.assertIsNone(skipped.popisano)
        self.assertEqual(entry.popis_status, Uvoz.PopisStatus.U_TOKU)
        self.assertFalse(entry.zaliha_primljena)
        self.assertFalse(WarehouseStock.objects.exists())

    def test_finished_inventory_cannot_be_changed(self):
        entry = Uvoz.objects.create(naziv='Finished', popis_status='zavrsen')
        with self.assertRaises(MagacinError):
            mark_all_uvoz_counted(entry)

    def test_missing_expected_quantity_changes_nothing(self):
        entry = Uvoz.objects.create(naziv='Invalid')
        line = UvozStavka.objects.create(uvoz=entry, artikal_naziv='First', kolicina=5, popisano=2)
        UvozStavka.objects.create(uvoz=entry, artikal_naziv='Missing quantity')
        with self.assertRaises(MagacinError):
            mark_all_uvoz_counted(entry)
        line.refresh_from_db()
        self.assertEqual(line.popisano, 2)
