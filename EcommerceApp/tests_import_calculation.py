import json
from decimal import Decimal
from django.test import TestCase, SimpleTestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from .import_calculation import calculation, calculate_rows
from .models import Product, Uvoz, UvozStavka


class CalculationValidationTests(SimpleTestCase):
    def test_rate_excludes_vat_and_accepts_decimal_comma(self):
        values = calculation({'kurs': '1,95', 'iznos_fakture': '1000', 'carina': '50', 'prevoz': '100', 'ostali_troskovi': '50', 'pdv': '999'})
        self.assertEqual(Decimal(values['trosak_bez_pdv_pct']), Decimal('20'))
    def test_invalid_and_zero_denominators_rejected(self):
        for data in [{'kurs': '0', 'iznos_fakture': '10'}, {'kurs': '1', 'iznos_fakture': '0'}, {'kurs': 'NaN', 'iznos_fakture': '1'}, {'kurs': '1', 'iznos_fakture': '10', 'carina': '-1'}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                calculation(data)


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ImportCalculationTests(TestCase):
    def setUp(self):
        self.config = calculation({'kurs': '2', 'iznos_fakture': '1000', 'prevoz': '100', 'carina': '50', 'ostali_troskovi': '50', 'pdv': '170', 'jci': 'JCI-1', 'faktura': 'F-1'})
        self.product = Product.objects.create(naziv='CALC01 FOX', cijena='32.29')
        self.user = get_user_model().objects.create_superuser('calcadmin', 'calc@example.com', 'test')
        self.client.force_login(self.user)
    def test_all_formulas_and_existing_mpc(self):
        row = calculate_rows([{'artikal': self.product.naziv, 'kolicina': Decimal('3'), 'fakturna': Decimal('10'), 'nabavna': Decimal('999')}], self.config)[0]
        self.assertEqual(row['nabavna'], Decimal('24'))
        self.assertEqual(row['mpc_brutto'], Decimal('32.29'))
        self.assertEqual(row['vpc_netto'], Decimal('19.9988'))
        self.assertEqual(row['vpc_marza'], Decimal('83.328333'))
        self.assertEqual(row['ukupno_fakturna'], Decimal('30'))
    def test_new_import_persists_config_and_recomputes_tampered_fields(self):
        rows = [{'artikal': self.product.naziv, 'kolicina': '3', 'fakturna': '10', 'mpc_brutto': '32.29', 'nabavna': '999', 'vpc_netto': '999', 'vpc_marza': '999'}]
        response = self.client.post(reverse('staff_magacin_uvoz_novi'), {'naziv': 'Calculation import', 'rows_json': json.dumps(rows), 'calculation_json': json.dumps(self.config)})
        self.assertEqual(response.status_code, 302)
        entry = Uvoz.objects.get()
        self.assertEqual(entry.kalkulacija['jci'], 'JCI-1')
        self.assertEqual(entry.stavke.get().nabavna, Decimal('24'))
    def test_saved_import_calculation_recalculates_lines(self):
        entry = Uvoz.objects.create(naziv='Saved calculation', izvor='magacin')
        line = UvozStavka.objects.create(uvoz=entry, artikal_naziv=self.product.naziv, kolicina=3, fakturna=10, mpc_brutto='32.29')
        response = self.client.post(reverse('staff_magacin_uvoz_detail', args=[entry.pk]), dict(self.config, action='save_calculation'))
        self.assertEqual(response.status_code, 302)
        line.refresh_from_db(); entry.refresh_from_db()
        self.assertEqual(line.nabavna, Decimal('24'))
        self.assertEqual(entry.kalkulacija['faktura'], 'F-1')
