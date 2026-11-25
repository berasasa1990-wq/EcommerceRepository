from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Akcija, Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class BulkActionsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('actions', 'actions@example.com', 'test')
        self.client.force_login(self.admin)
        self.url = reverse('staff_bulk_actions')
        self.first = Product.objects.create(naziv='Eva prva', cijena=100, na_stanju=True, stanje=5)
        self.second = Product.objects.create(naziv='Eva druga', cijena=50, na_stanju=True, stanje=3)
        self.other = Product.objects.create(naziv='Drugo', cijena=80)
        self.end = timezone.localdate() + timedelta(days=7)

    def post(self, **updates):
        data = {'artikal': [self.first.pk, self.second.pk], 'percent': '20', 'end_date': self.end.isoformat()}
        data.update(updates)
        return self.client.post(self.url, data)

    def test_catalog_search_and_page(self):
        self.assertContains(self.client.get(self.url), 'Bulk Akcije')
        self.assertContains(self.client.get(self.url), 'Kompletan lager')
        self.assertContains(self.client.get(reverse('staff_admin_panel')), self.url)
        data = self.client.get(self.url, {'catalog': '1', 'q': 'Eva'}).json()
        self.assertEqual(data['total'], 2)
        self.assertEqual({row['id'] for row in data['results']}, {self.first.pk, self.second.pk})

    def test_catalog_excludes_unavailable_and_zero_stock(self):
        Product.objects.create(naziv='Eva nedostupna', cijena=10, na_stanju=False, stanje=5)
        Product.objects.create(naziv='Eva prazna', cijena=10, na_stanju=True, stanje=0)
        for query in ('', 'Eva'):
            data = self.client.get(self.url, {'catalog': '1', 'q': query}).json()
            self.assertEqual({row['id'] for row in data['results']}, {self.first.pk, self.second.pk})

    def test_only_selected_products_discounted_and_expire(self):
        self.assertEqual(self.post().status_code, 200)
        for product in (self.first, self.second):
            product.refresh_from_db()
            self.assertEqual(product.akcija_postotak, Decimal('20'))
            self.assertEqual(product.akcija_do, self.end)
            self.assertEqual(product.akcijska_cijena, product.cijena * Decimal('.8'))
        self.other.refresh_from_db()
        self.assertIsNone(self.other.akcija_postotak)
        with patch('EcommerceApp.models.timezone.localdate', return_value=self.end + timedelta(days=1)):
            self.assertEqual(self.first.prikazna_cijena, self.first.cijena)

    def test_invalid_input_does_not_apply_partial_changes(self):
        for changes in ({'percent': 'NaN'}, {'percent': 'Infinity'}, {'percent': '100'}, {'percent': '0'}, {'artikal': []}, {'end_date': ''}, {'end_date': (timezone.localdate()-timedelta(days=1)).isoformat()}, {'artikal': [self.first.pk, 99999999]}):
            self.assertEqual(self.post(**changes).status_code, 400)
        self.first.refresh_from_db()
        self.assertIsNone(self.first.akcija_postotak)

    def test_complete_stock_schedule_preserves_previous_prices(self):
        Product.objects.filter(pk=self.first.pk).update(akcijska_cijena=70, akcija_do=self.end)
        self.first.refresh_from_db()
        start = timezone.localtime().replace(second=0, microsecond=0) + timedelta(minutes=5)
        end = start + timedelta(hours=1)
        response = self.client.post(self.url, {
            'scope': 'stock', 'percent': '40',
            'starts_at': start.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': end.strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(response.status_code, 200, response.content)
        sale = Akcija.objects.get(kompletan_lager=True)
        self.assertEqual((sale.pocetak, sale.kraj_lagera), (start, end))
        self.assertEqual(self.first.prikazna_cijena, Decimal('70'))
        with patch('EcommerceApp.models.timezone.now', return_value=start):
            self.assertEqual(self.first.prikazna_cijena, Decimal('60'))
            self.assertEqual(self.second.prikazna_cijena, Decimal('30'))
            self.assertEqual(self.other.prikazna_cijena, Decimal('80'))
        with patch('EcommerceApp.models.timezone.now', return_value=end):
            self.assertEqual(self.first.prikazna_cijena, Decimal('70'))
            self.assertEqual(self.second.prikazna_cijena, Decimal('50'))
        self.first.refresh_from_db()
        self.assertEqual(self.first.akcijska_cijena, Decimal('70'))

    def test_complete_stock_requires_valid_dates_in_order(self):
        start = (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        for end in ('', 'neispravno', start):
            response = self.client.post(self.url, {
                'scope': 'stock', 'percent': '20', 'starts_at': start, 'ends_at': end,
            })
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Akcija.objects.filter(kompletan_lager=True).exists())

    def test_sale_remaining_label_updates_and_expires(self):
        from django.template.loader import render_to_string
        self.post(end_date=(timezone.localdate() + timedelta(days=2)).isoformat())
        self.first.refresh_from_db()
        template = 'partials/product_sale_remaining.html'
        self.assertIn('Još 2 dana', render_to_string(template, {'product': self.first}))
        with patch('EcommerceApp.models.timezone.localdate', return_value=self.first.akcija_do):
            self.assertIn('Danas ističe', render_to_string(template, {'product': self.first}))
        with patch('EcommerceApp.models.timezone.localdate', return_value=self.first.akcija_do + timedelta(days=1)):
            self.assertEqual(render_to_string(template, {'product': self.first}).strip(), '')

    def test_staff_without_admin_rights_cannot_apply(self):
        user = User.objects.create_user('staff-actions', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.post().status_code, 302)
        self.first.refresh_from_db()
        self.assertIsNone(self.first.akcija_postotak)
