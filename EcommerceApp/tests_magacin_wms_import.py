import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Product, ProductWMSStock, WarehouseLocation, WarehouseStock, WMSLocation


class MagacinWMSImportTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Import test', cijena=10, stanje=7, na_stanju=True)
        self.a = WarehouseLocation.objects.create(sifra='A01', naziv='Polica A', redoslijed=1)
        self.b = WarehouseLocation.objects.create(sifra='B02', naziv='B02', redoslijed=2)
        WarehouseStock.objects.create(product=self.product, location=self.a, kolicina=0)
        WarehouseStock.objects.create(product=self.product, location=self.b, kolicina=8, rezervisano=1)
        self.garage = WMSLocation.objects.create(naziv='Garaza')

    def apply(self, path):
        call_command('import_magacin_locations', apply=True, backup=str(path), stdout=io.StringIO())

    def test_preview_does_not_write(self):
        output = io.StringIO()
        call_command('import_magacin_locations', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['physical_quantity'], 8)
        self.assertEqual(WMSLocation.objects.count(), 1)
        self.assertFalse(ProductWMSStock.objects.exists())
        self.product.refresh_from_db()
        self.assertIsNone(self.product.wms_lokacija_id)

    def test_exact_locations_quantities_and_legacy_reservations_are_preserved(self):
        with TemporaryDirectory() as tmp:
            backup = Path(tmp) / 'before.json'
            self.apply(backup)
            self.assertEqual(json.loads(backup.read_text())['wms_locations'][0]['naziv'], 'Garaza')
        self.assertEqual(dict(ProductWMSStock.objects.values_list('lokacija__naziv', 'kolicina')), {'A01': 0, 'B02': 8})
        self.assertEqual(WMSLocation.objects.get(naziv='A01').opis, 'Polica A')
        self.product.refresh_from_db()
        self.assertEqual(self.product.wms_lokacija.naziv, 'B02')
        self.assertEqual(self.product.stanje, 7)
        self.assertTrue(WMSLocation.objects.filter(pk=self.garage.pk).exists())
        self.assertEqual(WarehouseStock.objects.get(location=self.b).rezervisano, 1)
        self.assertEqual(WarehouseStock.objects.get(location=self.b).kolicina, 8)

    def test_second_import_does_not_duplicate_stock(self):
        with TemporaryDirectory() as tmp:
            self.apply(Path(tmp) / 'first.json')
            self.apply(Path(tmp) / 'second.json')
        self.assertEqual(ProductWMSStock.objects.count(), 2)
        self.assertEqual(WMSLocation.objects.count(), 3)

    def test_conflict_leaves_everything_unchanged(self):
        target = WMSLocation.objects.create(naziv='B02')
        ProductWMSStock.objects.create(product=self.product, lokacija=target, kolicina=9)
        with TemporaryDirectory() as tmp, self.assertRaises(CommandError):
            self.apply(Path(tmp) / 'before.json')
        self.assertEqual(ProductWMSStock.objects.get().kolicina, 9)
        self.assertFalse(WMSLocation.objects.filter(naziv='A01').exists())

    def test_negative_quantity_is_rejected_without_writes(self):
        WarehouseStock.objects.filter(location=self.a).update(kolicina=-1)
        with self.assertRaises(CommandError):
            call_command('import_magacin_locations', stdout=io.StringIO())
        self.assertFalse(ProductWMSStock.objects.exists())


class MagacinWMSWebImportTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        self.owner = get_user_model().objects.create_superuser(username='import-owner', password='test-only')
        self.client.force_login(self.owner)
        self.url = reverse('staff_wms_magacin_import')
        self.settings_url = reverse('staff_wms_section', args=['podesavanje'])
        self.product = Product.objects.create(naziv='Web import', cijena=10, stanje=4)
        self.location = WarehouseLocation.objects.create(sifra='WEB01', naziv='Polica')
        WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=5, rezervisano=1)

    def test_settings_show_option_and_source_counts_without_stock_writes(self):
        response = self.client.get(self.settings_url)
        self.assertContains(response, 'Prenesi lokacije i količine iz magacina')
        self.assertEqual(response.context['magacin_import'], {'locations': 1, 'products': 1, 'quantity': 5})
        self.assertFalse(ProductWMSStock.objects.exists())

    def test_post_imports_and_repeat_does_not_duplicate(self):
        for _ in range(2):
            response = self.client.post(self.url, follow=True)
            self.assertContains(response, 'Prenos iz magacina je završen')
        self.assertEqual(ProductWMSStock.objects.count(), 1)
        self.assertEqual(ProductWMSStock.objects.get().kolicina, 5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.wms_lokacija.naziv, 'WEB01')
        self.assertEqual(self.product.stanje, 4)
        self.assertEqual(WarehouseStock.objects.get().rezervisano, 1)

    def test_get_cannot_run_import(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertFalse(ProductWMSStock.objects.exists())

    def test_anonymous_and_non_superuser_cannot_import(self):
        from django.contrib.auth import get_user_model
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        staff = get_user_model().objects.create_user(username='ordinary-staff', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertFalse(ProductWMSStock.objects.exists())

    def test_csrf_is_required(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assertFalse(ProductWMSStock.objects.exists())

    def test_locked_stock_module_blocks_action_and_hides_option(self):
        from .models import ModulePermissions
        ModulePermissions.objects.create(pk=1, wms_zalihe=False)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertNotContains(self.client.get(self.settings_url), 'Prenesi lokacije i količine iz magacina')
        self.assertFalse(ProductWMSStock.objects.exists())

    def test_conflict_shows_error_without_partial_changes(self):
        target = WMSLocation.objects.create(naziv='WEB01')
        ProductWMSStock.objects.create(product=self.product, lokacija=target, kolicina=9)
        response = self.client.post(self.url, follow=True)
        self.assertContains(response, 'već ima drugačiju WMS zalihu')
        self.assertEqual(ProductWMSStock.objects.get().kolicina, 9)
        self.product.refresh_from_db()
        self.assertIsNone(self.product.wms_lokacija_id)

    def test_regular_settings_save_still_works(self):
        from .models import WMSSettings
        response = self.client.post(self.settings_url, {'vat_rate': '17'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WMSSettings.objects.get(pk=1).vat_rate, 17)
        self.assertFalse(ProductWMSStock.objects.exists())
