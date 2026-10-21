import json
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import BulkPriceBatch, Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class BulkPricesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('bulk-admin', 'bulk@example.com', 'test')
        self.client.force_login(self.user)
        self.product = Product.objects.create(naziv='Bulk artikal', magacin_sync_at=timezone.now(), cijena=12, barkod='3871234567890', stanje=20)
        self.other = Product.objects.create(naziv='Drugi artikal', magacin_sync_at=timezone.now(), cijena=5, barkod='3871234567891')
        self.rows = [dict(token=str(self.product.pk), barcode=self.product.barkod, quantity=3),
                     dict(token=str(self.other.pk), barcode=self.other.barkod, quantity=2)]
        for name in ('_etiketa_barcode_data_uri', '_etiketa_qr_data_uri'):
            mock = patch('EcommerceApp.views_magacin.' + name, return_value='')
            mock.start()
            self.addCleanup(mock.stop)

    def save_batch(self, rows=None):
        return self.client.post(reverse('staff_magacin_cijene_bulk_save'),
                                json.dumps({'items': self.rows if rows is None else rows}), content_type='application/json')

    def test_shared_notification_and_print_quantities(self):
        self.assertContains(self.client.get(reverse('staff_magacin_cijene_bulk')), 'Skeniraj kamerom')
        response = self.save_batch()
        self.assertEqual(response.status_code, 200)
        batch = BulkPriceBatch.objects.get(pk=response.json()['id'])
        other_user = User.objects.create_superuser('desktop-admin', 'desktop@example.com', 'test')
        self.client.force_login(other_user)
        self.assertContains(self.client.get(reverse('staff_magacin_stampa_cijena')), 'Lista #' + str(batch.pk))
        for paper in ('a4', 'zebra'):
            printed = self.client.post(reverse('staff_magacin_cijene_bulk_print', args=[batch.pk]), {'papir': paper})
            self.assertEqual(printed.context['etiketa_count'], 5)
            if paper == 'zebra':
                items = printed.context['items']
                self.assertEqual([item['barkod'] for item in items], [self.product.barkod] * 3 + [self.other.barkod] * 2)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stanje, 20)

    def test_invalid_barcode_and_quantity_are_not_saved(self):
        for changes in ({'barcode': 'WRONG'}, {'quantity': 0}, {'quantity': 281}, {'quantity': 1.5}, {'quantity': True}):
            row = dict(self.rows[0], **changes)
            self.assertEqual(self.save_batch([row]).status_code, 400)
        self.assertEqual(self.save_batch([]).status_code, 400)
        self.assertFalse(BulkPriceBatch.objects.exists())

    def test_barcode_is_optional_and_print_uses_existing_article_code(self):
        rows = [dict(self.rows[0], barcode='')]
        response = self.save_batch(rows)
        self.assertEqual(response.status_code, 200)
        batch = BulkPriceBatch.objects.get(pk=response.json()['id'])
        printed = self.client.post(reverse('staff_magacin_cijene_bulk_print', args=[batch.pk]), {'papir': 'zebra'})
        self.assertEqual(printed.context['etiketa_count'], 3)
        self.assertEqual(printed.context['items'][0]['barkod'], self.product.barkod)
        self.product.barkod = ''
        self.product.sifra = 'BULK-123'
        self.product.save()
        response = self.save_batch([{'token': str(self.product.pk), 'quantity': 2}])
        self.assertEqual(response.status_code, 200)
        printed = self.client.post(reverse('staff_magacin_cijene_bulk_print', args=[response.json()['id']]), {'papir': 'zebra'})
        self.assertEqual(printed.context['items'][0]['barkod'], 'BULK-123')

    def test_authentication_and_post_required(self):
        self.assertEqual(self.client.get(reverse('staff_magacin_cijene_bulk_save')).status_code, 405)
        self.client.logout()
        self.assertEqual(self.save_batch().status_code, 302)
        self.assertFalse(BulkPriceBatch.objects.exists())
