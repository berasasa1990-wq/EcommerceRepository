from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Order, WarehouseCustomer
from .views_magacin import _save_warehouse_customer
from .warehouse_customers_ledger import ensure_order_partners


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CustomerUniqueNameTests(TestCase):
    def test_save_reuses_normalized_name_with_new_phone(self):
        original = _save_warehouse_customer(ime='Ana Ribić', telefon='061111111')
        saved = _save_warehouse_customer(ime='  ANA   RIBIĆ ', telefon='062222222')
        self.assertEqual(saved.pk, original.pk)
        self.assertEqual(WarehouseCustomer.objects.count(), 1)
        self.assertEqual(saved.telefon, '062222222')

    def test_existing_duplicate_names_appear_once(self):
        original = WarehouseCustomer.objects.create(ime_prezime='Ana Ribić', telefon='061111111')
        WarehouseCustomer.objects.create(ime_prezime=' ANA  RIBIĆ ', telefon='062222222')
        user = User.objects.create_superuser('unique-admin', 'admin@example.com', 'pass')
        self.client.force_login(user)
        response = self.client.get(reverse('staff_magacin_kupci'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['customer_count'], 1)
        self.assertEqual(list(response.context['customers']), [original])

    def test_history_does_not_create_same_name_again(self):
        original = WarehouseCustomer.objects.create(ime_prezime='Ana Ribić', telefon='061111111')
        Order.objects.create(broj='UNIQUE-NAME', ime_prezime='ANA  RIBIĆ', telefon='062222222', ukupno=0)
        ensure_order_partners()
        self.assertEqual(list(WarehouseCustomer.objects.all()), [original])
