from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Product, Uvoz, UvozStavka, WarehouseStock, WarehouseMovement


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class UvozPricePrintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('price-admin', 'price@example.com', 'test')
        self.client.force_login(self.user)
        self.product = Product.objects.create(naziv='Artikal A', cijena=12, stanje=20)
        self.other = Product.objects.create(naziv='Artikal B', cijena=8)
        self.uvoz = Uvoz.objects.create(naziv='Test uvoz', izvor=Uvoz.Izvor.MAGACIN)
        self.row = UvozStavka.objects.create(uvoz=self.uvoz, product=self.product, artikal_naziv='Artikal A', kolicina=3)
        self.duplicate = UvozStavka.objects.create(uvoz=self.uvoz, product=self.product, artikal_naziv='Artikal A', kolicina=2)
        self.other_row = UvozStavka.objects.create(uvoz=self.uvoz, product=self.other, artikal_naziv='Artikal B', kolicina=4)
        self.url = reverse('staff_magacin_uvoz_cijene', args=[self.uvoz.pk])
        for name in ('_etiketa_barcode_data_uri', '_etiketa_qr_data_uri'):
            mock = patch('EcommerceApp.views_magacin.' + name, return_value='')
            mock.start()
            self.addCleanup(mock.stop)

    def test_selection_page_and_import_links(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Označi sve')
        self.assertContains(response, 'value="zebra"')
        self.assertContains(response, 'value="a4"')
        self.assertEqual(len(response.context['stavke']), 3)
        self.assertContains(self.client.get(reverse('staff_magacin_uvoz_detail', args=[self.uvoz.pk])), self.url)
        self.uvoz.izvor = Uvoz.Izvor.SAJT
        self.uvoz.save(update_fields=['izvor'])
        self.assertContains(self.client.get(reverse('staff_uvoz_detail', args=[self.uvoz.pk])), self.url)

    def test_one_per_product_deduplicates_selected_rows(self):
        response = self.client.post(self.url, {'stavka': [self.row.pk, self.duplicate.pk, self.other_row.pk], 'broj': 'jedna', 'papir': 'a4'})
        self.assertTemplateUsed(response, 'staff/magacin/artikal_etiketa.html')
        self.assertEqual(response.context['etiketa_count'], 2)

    def test_quantity_uses_import_not_current_stock_and_preserves_data(self):
        response = self.client.post(self.url, {'stavka': [self.row.pk, self.duplicate.pk], 'broj': 'kolicina', 'papir': 'zebra'})
        self.assertTemplateUsed(response, 'staff/magacin/artikal_etiketa_zebra.html')
        self.assertEqual(response.context['etiketa_count'], 5)
        self.assertEqual({row['naziv'] for row in response.context['items']}, {'Artikal A'})
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 20)
        self.assertEqual(self.product.cijena, Decimal('12'))
        self.assertFalse(WarehouseStock.objects.exists())
        self.assertFalse(WarehouseMovement.objects.exists())

    def test_foreign_rows_and_empty_selection_cannot_print(self):
        other_import = Uvoz.objects.create(naziv='Drugi uvoz')
        foreign = UvozStavka.objects.create(uvoz=other_import, product=self.other, artikal_naziv='Drugi', kolicina=9)
        for selected in ([], [foreign.pk]):
            response = self.client.post(self.url, {'stavka': selected, 'broj': 'jedna'})
            self.assertContains(response, 'Izaberi barem jedan artikal')
            self.assertNotIn('etiketa_count', response.context)

    def test_missing_products_are_not_selectable(self):
        UvozStavka.objects.create(uvoz=self.uvoz, artikal_naziv='Nema proizvoda', kolicina=3)
        self.assertNotContains(self.client.get(self.url), 'Nema proizvoda')

    def test_invalid_quantities_fail_without_silent_truncation(self):
        for qty in (Decimal('1.5'), Decimal('0'), Decimal('-1'), None, Decimal('10001')):
            self.row.kolicina = qty
            self.row.save(update_fields=['kolicina'])
            response = self.client.post(self.url, {'stavka': [self.row.pk], 'broj': 'kolicina'})
            self.assertTrue(response.context['print_error'])
            self.assertNotIn('etiketa_count', response.context)

    def test_non_admin_cannot_access_or_print(self):
        user = User.objects.create_user('ordinary', password='test')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'stavka': [self.row.pk]}).status_code, 302)
