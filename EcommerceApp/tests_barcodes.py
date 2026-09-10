from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.forms import modelform_factory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Product, BarcodeConflict


@override_settings(ALLOWED_HOSTS=['testserver'], SITE_PREP_ENABLED=False,
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class BarcodeTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser('barcode-admin', 'b@example.com', 'secret723!')
        self.owner = Product.objects.create(naziv='Prvi artikal', sifra='FIRST', cijena=Decimal('10'),
                                           barkod='38700123', magacin_sync_at=timezone.now())
        self.target = Product.objects.create(naziv='Drugi artikal', sifra='SECOND', cijena=Decimal('12'),
                                            barkod='OTHER', magacin_sync_at=timezone.now())
        self.client.force_login(self.staff)

    def test_duplicate_save_blocked_and_both_products_recorded(self):
        self.target.barkod = ' 38700123 '
        with self.assertRaises(ValidationError):
            self.target.save()
        self.target.refresh_from_db()
        self.assertEqual(self.target.barkod, 'OTHER')
        record = BarcodeConflict.objects.get()
        self.assertEqual(record.product_id, self.target.pk)
        self.assertEqual(record.other_product_id, self.owner.pk)
        self.assertEqual(record.barcode, '38700123')
        self.assertFalse(record.existing_duplicate)

    def test_live_check_logs_immediately_and_prevents_edit_save(self):
        check = self.client.post(reverse('staff_magacin_barkod_provjera'), {
            'product_id': self.target.pk, 'barkod': '38700123'})
        self.assertEqual(check.status_code, 409)
        self.assertTrue(check.json()['duplicate'])
        response = self.client.post(reverse('staff_magacin_artikal_izmjena', args=[self.target.pk]), {
            'naziv': 'Ne smije se sačuvati', 'sifra': 'SECOND', 'barkod': '38700123', 'cijena': '99'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dupli barkod')
        self.assertContains(response, 'duplicate-barcode-message')
        self.target.refresh_from_db()
        self.assertEqual(self.target.naziv, 'Drugi artikal')
        self.assertEqual(self.target.cijena, Decimal('12'))
        self.assertEqual(self.target.barkod, 'OTHER')
        self.assertEqual(BarcodeConflict.objects.count(), 1)
        self.assertEqual(BarcodeConflict.objects.get().attempts, 2)
        page = self.client.get(reverse('staff_magacin_dupli_barkodovi'))
        self.assertContains(page, 'Prvi artikal')
        self.assertContains(page, 'Drugi artikal')
        self.assertContains(page, 'Dupli barkod — unos blokiran')
        self.assertContains(page, 'barcode-pair')

    def test_blank_self_and_unique_barcode_allowed(self):
        self.owner.save()
        for barcode in ['', ' 987654321 ']:
            self.target.barkod = barcode
            self.target.save()
            self.target.refresh_from_db()
            self.assertEqual(self.target.barkod, barcode.strip())
        self.assertFalse(BarcodeConflict.objects.exists())

    def test_case_insensitive_and_unsaved_product_admin_form(self):
        self.owner.barkod = 'AbC123'
        self.owner.save()
        Form = modelform_factory(Product, fields=['naziv', 'sifra', 'barkod', 'cijena'])
        form = Form(data={'naziv': 'Novi artikal', 'sifra': 'NEW', 'barkod': ' abc123 ', 'cijena': '12'})
        self.assertFalse(form.is_valid())
        self.assertIn('barkod', form.errors)
        record = BarcodeConflict.objects.get()
        self.assertIsNone(record.product_id)
        self.assertEqual(record.product_name, 'Novi artikal')
        self.assertEqual(record.other_product_id, self.owner.pk)
        self.assertFalse(Product.objects.filter(sifra='NEW').exists())

    def test_existing_duplicates_are_imported_and_can_be_corrected(self):
        Product.objects.filter(pk=self.target.pk).update(barkod=' 38700123 ')
        migration = import_module('EcommerceApp.migrations.0265_duplicate_barcodes')
        migration.record_existing_duplicates(apps, SimpleNamespace(connection=connection))
        migration.record_existing_duplicates(apps, SimpleNamespace(connection=connection))
        self.assertEqual(BarcodeConflict.objects.count(), 1)
        page = self.client.get(reverse('staff_magacin_dupli_barkodovi'))
        self.assertContains(page, 'Dupli barkod na oba artikla')
        self.target.refresh_from_db()
        self.target.naziv = 'Izmijenjen naziv'
        self.target.save()
        self.target.barkod = 'NOVI-UNIKATNI'
        self.target.save()
        page = self.client.get(reverse('staff_magacin_dupli_barkodovi'))
        self.assertContains(page, 'Raniji duplikat — barkod je promijenjen')
        self.assertNotContains(page, 'Dupli barkod na oba artikla')

    def test_page_and_check_require_warehouse_access(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('staff_magacin_dupli_barkodovi')).status_code, 302)
        self.assertEqual(self.client.post(reverse('staff_magacin_barkod_provjera'), {
            'product_id': self.target.pk, 'barkod': self.owner.barkod}).status_code, 302)
        self.assertFalse(BarcodeConflict.objects.exists())

    def test_admin_widget_has_live_check(self):
        page = self.client.get(reverse('admin:EcommerceApp_product_change', args=[self.target.pk]))
        self.assertContains(page, 'data-barcode-check=')
        self.assertContains(page, 'js/barcode-check.js')

    def test_delete_record_preserves_products_and_duplicate_protection(self):
        from .barcodes import validate_barcode
        with self.assertRaises(ValidationError):
            validate_barcode(self.target, self.owner.barkod)
        record = BarcodeConflict.objects.get()
        url = reverse('staff_magacin_dupli_barkod_obrisi', args=[record.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(BarcodeConflict.objects.filter(pk=record.pk).exists())
        self.client.force_login(self.staff)
        response = self.client.post(url, {'q': '38700123', 'page': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(BarcodeConflict.objects.exists())
        self.owner.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.owner.barkod, '38700123')
        self.assertEqual(self.target.barkod, 'OTHER')
        with self.assertRaises(ValidationError):
            validate_barcode(self.target, self.owner.barkod)
        self.assertEqual(BarcodeConflict.objects.count(), 1)

    def test_print_includes_all_filtered_records_across_pages(self):
        BarcodeConflict.objects.bulk_create([
            BarcodeConflict(key=f'print-{i}', barcode=f'PRINT-{i}', product=self.target,
                            other_product=self.owner, product_name=self.target.naziv,
                            other_name=self.owner.naziv) for i in range(35)
        ])
        url = reverse('staff_magacin_dupli_barkodovi')
        listing = self.client.get(url)
        self.assertContains(listing, 'Štampaj listu')
        self.assertEqual(len(listing.context['page']), 30)
        printed = self.client.get(url, {'print': '1'})
        self.assertEqual(len(printed.context['records']), 35)
        self.assertContains(printed, 'PRINT-0')
        self.assertContains(printed, 'PRINT-34')
        self.assertContains(printed, 'Prvi artikal')
        self.assertContains(printed, 'Drugi artikal')
        self.assertNotContains(printed, 'Obriši evidenciju')
        filtered = self.client.get(url, {'print': '1', 'q': 'PRINT-34'})
        self.assertEqual(len(filtered.context['records']), 1)
        self.client.logout()
        self.assertEqual(self.client.get(url, {'print': '1'}).status_code, 302)
