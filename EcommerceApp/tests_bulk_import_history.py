from decimal import Decimal
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from .models import Uvoz, UvozStavka, WarehouseStock
from .warehouse_import_check import excel_import_values, attach_import_values, save_bulk_import_history, import_history_tsv


class ImportSnapshotParsingTests(SimpleTestCase):
    def test_excel_metadata_is_attached_to_matching_name(self):
        values = excel_import_values('Šifra\tKoličina\tFakturna\nABC01\t4\t12,50\nXYZ02\t2\t3,25')
        rows = attach_import_values([{'naziv': 'XYZ02 FOX torbica', 'brand_id': '1'}, {'naziv': 'ABC01 FOX štap', 'brand_id': '1'}], values)
        self.assertEqual(rows[0]['kolicina'], '2')
        self.assertEqual(rows[1]['fakturna'], '12,50')

    def test_tsv_keeps_zero_values_and_escapes_excel_formula(self):
        text = import_history_tsv([SimpleNamespace(artikal_naziv='=FORMULA', kolicina=Decimal('0'), fakturna=Decimal('12.50'))])
        self.assertIn("'=FORMULA\t0\t12.50", text)


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class BulkImportHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('historyadmin', 'history@example.com', 'test')
        self.client.force_login(self.user)

    def test_snapshot_preserves_invoice_and_quantity_without_stock_changes(self):
        imported = save_bulk_import_history([{'naziv': 'ABC FOX štap', 'kolicina': '4', 'fakturna': '12,50'}], [object()], [], self.user)
        line = imported.stavke.get()
        self.assertEqual(line.kolicina, Decimal('4'))
        self.assertEqual(line.fakturna, Decimal('12.50'))
        self.assertFalse(imported.zaliha_primljena)
        self.assertFalse(WarehouseStock.objects.exists())
        response = self.client.get(reverse('staff_magacin_novi_uvoz_istorija_detail', args=[imported.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ABC FOX štap')
        self.assertContains(response, 'Kopiraj za Excel')
        self.assertIn('ABC FOX štap\t4.000\t12.5000', response.context['import_excel_text'])

    def test_bad_numbers_fail_before_history_creation(self):
        with self.assertRaises(ValueError):
            save_bulk_import_history([{'naziv': 'ABC', 'kolicina': 'nije broj'}], [], [], self.user)
        self.assertFalse(Uvoz.objects.exists())

    def test_history_has_ten_latest_imports_and_excludes_site_imports(self):
        Uvoz.objects.create(naziv='Sajt only', izvor=Uvoz.Izvor.SAJT)
        for i in range(12):
            Uvoz.objects.create(naziv=f'Magacin {i}', izvor=Uvoz.Izvor.MAGACIN, napomena='[novi-artikli-unos]')
        response = self.client.get(reverse('staff_magacin_novi_uvozi_istorija'))
        self.assertEqual(len(response.context['imports_page']), 10)
        self.assertEqual(response.context['imports_page'].paginator.count, 12)
        self.assertNotContains(response, 'Sajt only')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('staff_magacin_novi_uvozi_istorija')).status_code, 302)

    def test_final_bulk_save_creates_product_and_history_together(self):
        from .models import Brand, Product
        brand = Brand.objects.create(naziv='History FOX')
        session = self.client.session
        session['warehouse_bulk_ready'] = {'token': 'history-token', 'rows': [{'naziv': 'XYZ99 History FOX štap', 'brand_id': str(brand.pk), 'opis': 'Opis štapa.', 'kolicina': '3', 'fakturna': '18,25'}]}
        session.save()
        response = self.client.post(reverse('staff_magacin_opisi_bulk'), {'action': 'create', 'draft_token': 'history-token'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Product.objects.get(naziv='XYZ99 History FOX štap').opis, 'Opis štapa.')
        line = UvozStavka.objects.get()
        self.assertEqual(line.kolicina, Decimal('3'))
        self.assertEqual(line.fakturna, Decimal('18.25'))
        self.assertNotIn('warehouse_bulk_ready', self.client.session)

    def test_history_failure_rolls_back_article_and_preserves_draft(self):
        from .models import Brand, Product
        brand = Brand.objects.create(naziv='History FOX')
        session = self.client.session
        session['warehouse_bulk_ready'] = {'token': 'history-token', 'rows': [{'naziv': 'BAD01 FOX štap', 'brand_id': str(brand.pk), 'opis': 'Opis.', 'kolicina': 'neispravno'}]}
        session.save()
        response = self.client.post(reverse('staff_magacin_opisi_bulk'), {'action': 'create', 'draft_token': 'history-token'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(naziv='BAD01 FOX štap').exists())
        self.assertFalse(Uvoz.objects.exists())
        self.assertIn('warehouse_bulk_ready', self.client.session)

    def test_full_first_input_keeps_existing_and_new_codes_then_resolves_new_name(self):
        from .models import Product
        from .warehouse_import_check import save_input_history
        old = Product.objects.create(naziv='OLD01 FOX Existing', cijena=10)
        values = [{'code': 'OLD01', 'kolicina': '7', 'fakturna': '12.50'}, {'code': 'NEW02', 'kolicina': '3', 'fakturna': '4.25'}]
        entry = save_input_history(values, self.user)
        self.assertEqual(entry.stavke.count(), 2)
        self.assertEqual(entry.stavke.get(poruka='OLD01').artikal_naziv, old.naziv)
        Product.objects.create(naziv='NEW02 KORUM New', cijena=10)
        response = self.client.get(reverse('staff_magacin_novi_uvoz_istorija_detail', args=[entry.pk]))
        text = response.context['import_excel_text']
        self.assertIn('OLD01 FOX Existing\t7.000\t12.5000', text)
        self.assertIn('NEW02 KORUM New\t3.000\t4.2500', text)
        returned = save_bulk_import_history([{'naziv': 'NEW02 KORUM New', 'source_input_id': entry.pk}], [], [], self.user)
        self.assertEqual(returned.pk, entry.pk)
        self.assertEqual(entry.stavke.count(), 2)
        self.assertEqual(Uvoz.objects.count(), 1)

    def test_history_excludes_normal_warehouse_imports_and_shows_previous_session_input(self):
        from .models import Product
        Uvoz.objects.create(naziv='Real warehouse import', izvor=Uvoz.Izvor.MAGACIN)
        Product.objects.create(naziv='REC01 FOX Found later', cijena=10)
        session = self.client.session
        session['warehouse_excel_values'] = [{'code': 'REC01', 'kolicina': '8', 'fakturna': '9.50'}]
        session.save()
        response = self.client.get(reverse('staff_magacin_novi_uvozi_istorija') + '?posljednji=1')
        self.assertEqual(response.context['imports_page'].paginator.count, 0)
        self.assertIn('REC01 FOX Found later\t8\t9.50', response.context['import_excel_text'])
        self.assertNotContains(response, 'Real warehouse import')

    def test_legacy_bulk_record_without_marker_is_visible(self):
        entry = Uvoz.objects.create(naziv='Novi artikli bulk', izvor=Uvoz.Izvor.MAGACIN,
                                    napomena='Bulk unos naziva, brenda i opisa. Količine su iz izvornog Excela; ovaj unos ne mijenja lager.')
        UvozStavka.objects.create(uvoz=entry, artikal_naziv='Ranije unesen naziv', kolicina=2, fakturna='5.25')
        response = self.client.get(reverse('staff_magacin_novi_uvozi_istorija'))
        self.assertEqual(response.context['imports_page'].paginator.count, 1)
        response = self.client.get(reverse('staff_magacin_novi_uvoz_istorija_detail', args=[entry.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Ranije unesen naziv\t2.000\t5.2500', response.context['import_excel_text'])

    def test_previous_session_list_is_shown_without_extra_click(self):
        session = self.client.session
        session['warehouse_excel_values'] = [{'code': 'OLD99', 'kolicina': '6', 'fakturna': '2.50'}]
        session.save()
        response = self.client.get(reverse('staff_magacin_novi_uvozi_istorija'))
        self.assertTrue(response.context['session_input'])
        self.assertIn('OLD99\t6\t2.50', response.context['import_excel_text'])
        self.assertNotContains(response, 'Još nema sačuvanih')

    def test_warehouse_import_resolves_codes_and_preserves_each_rows_values(self):
        from .models import Product
        from .warehouse_import_check import resolve_warehouse_import_codes
        Product.objects.create(naziv='K0340051 KORUM mašinica', cijena=10)
        Product.objects.create(naziv='A0430110 PRESTON torbica', cijena=10)
        rows = resolve_warehouse_import_codes('Šifra\tKoličina\tFakturna\nA0430110\t3\t15.50\nK0340051\t7\t20,25')
        self.assertEqual(rows, [{'artikal': 'A0430110 PRESTON torbica', 'kolicina': '3', 'fakturna': '15.50', 'mpc_brutto': '10.00'}, {'artikal': 'K0340051 KORUM mašinica', 'kolicina': '7', 'fakturna': '20,25', 'mpc_brutto': '10.00'}])
        response = self.client.post(reverse('staff_magacin_uvoz_novi'), {'action': 'match_codes', 'paste_text': 'K0340051\t2\t9.50'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rows'][0]['artikal'], 'K0340051 KORUM mašinica')
        self.assertFalse(Uvoz.objects.exists())

    def test_warehouse_import_rejects_unknown_and_ambiguous_codes(self):
        from .models import Product
        from .warehouse_import_check import resolve_warehouse_import_codes
        Product.objects.create(naziv='ABC FOX prvi', cijena=10)
        Product.objects.create(naziv='ABC FOX drugi', cijena=10)
        for code in ['ABC', 'NEMA']:
            with self.subTest(code=code), self.assertRaises(ValueError):
                resolve_warehouse_import_codes(f'{code}\t2\t4.50')
        self.assertEqual(Product.objects.count(), 2)
