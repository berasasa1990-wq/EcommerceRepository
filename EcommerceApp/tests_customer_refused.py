from django.test import TestCase, override_settings
from django.urls import reverse
from . import tests_ledger as helpers
from .models import WarehouseCustomer
from .views_magacin import _save_warehouse_customer, _customer_payload


@override_settings(ALLOWED_HOSTS=['testserver'])
class CustomerRefusedTests(TestCase):
    setUp = helpers.WarehouseLedgerTests.setUp

    def test_customer_edit_sets_and_clears_warning(self):
        customer = WarehouseCustomer.objects.create(ime_prezime='Odbio Test', telefon='061987654')
        self.client.force_login(self.user)
        payload = {'customer_id': customer.pk, 'ime_prezime': customer.ime_prezime,
                   'telefon': customer.telefon, 'odbio_posiljku': '1'}
        response = self.client.post(reverse('staff_magacin_kupci'), payload)
        self.assertEqual(response.status_code, 302)
        customer.refresh_from_db()
        self.assertTrue(_customer_payload(customer)['odbio_posiljku'])
        response = self.client.get(reverse('staff_magacin_kupci_lookup'), {'q': 'Odbio Test'})
        self.assertTrue(response.json()['results'][0]['odbio_posiljku'])
        _save_warehouse_customer(customer_id=customer.pk, ime=customer.ime_prezime,
                                 telefon=customer.telefon, replace=True)
        customer.refresh_from_db()
        self.assertTrue(customer.odbio_posiljku)
        payload.pop('odbio_posiljku')
        self.client.post(reverse('staff_magacin_kupci'), payload)
        customer.refresh_from_db()
        self.assertFalse(customer.odbio_posiljku)
