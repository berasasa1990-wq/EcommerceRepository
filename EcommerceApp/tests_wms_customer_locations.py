from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import WMSCustomer, WMSLocation, ModulePermissions


class WMSCustomerLocationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='wms-options', password='test-only')
        self.client.force_login(self.user)
        self.url = reverse('staff_wms_order_customers')
        self.data = {'ime_prezime': 'Test kupac', 'telefon': '+387 (65) 123-456', 'adresa': 'Ulica 1', 'grad': 'Grad', 'postanski_broj': '71000'}

    def test_duplicate_phone_with_different_name_and_format_is_rejected(self):
        self.assertEqual(self.client.post(self.url, self.data).status_code, 200)
        data = {**self.data, 'ime_prezime': 'Drugi kupac', 'telefon': '38765123456'}
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('već postoji', response.json()['errors']['telefon'][0]['message'])
        self.assertEqual(WMSCustomer.objects.count(), 1)

    def test_edit_own_phone_works_but_another_customers_phone_is_rejected(self):
        first = WMSCustomer.objects.create(**self.data)
        second = WMSCustomer.objects.create(**{**self.data, 'telefon': '061222333'})
        url = reverse('staff_wms_manage_customer', args=[second.pk, 'izmjena'])
        response = self.client.post(url, {**self.data, 'telefon': first.telefon})
        self.assertContains(response, 'Kupac sa ovim brojem telefona već postoji.')
        second.refresh_from_db()
        self.assertEqual(second.telefon, '061222333')
        self.assertEqual(self.client.post(url, {**self.data, 'telefon': second.telefon}).status_code, 302)

    def test_customer_history_materialization_reuses_existing_phone(self):
        from .models import WMSOrder
        WMSCustomer.objects.create(**self.data)
        WMSOrder.objects.create(kupac='Ime iz historije', telefon='38765123456', tip='online')
        response = self.client.get(reverse('staff_wms_section', args=['kupci']))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WMSCustomer.objects.count(), 1)

    def test_invalid_phone_rejected(self):
        self.assertEqual(self.client.post(self.url, {**self.data, 'telefon': 'abc'}).status_code, 400)
        self.assertFalse(WMSCustomer.objects.exists())

    def test_locations_search_and_scroll_preserve_all_matches(self):
        WMSLocation.objects.bulk_create([WMSLocation(naziv=f'A{i:02}', opis='Polica') for i in range(15)])
        url = reverse('staff_wms_section', args=['lokacije'])
        response = self.client.get(url)
        self.assertEqual(len(response.context['records']), 15)
        self.assertContains(response, 'wms-locations-scroll')
        self.assertContains(response, 'Pretraži lokacije')
        response = self.client.get(url, {'q': 'A12'})
        self.assertEqual([r.naziv for r in response.context['records']], ['A12'])
        self.assertEqual(len(self.client.get(url, {'q': 'polica'}).context['records']), 15)
        self.assertContains(self.client.get(url, {'q': 'nepostojeca'}), 'Nema unesenih podataka.')

    def test_locations_and_customer_permissions_remain_enforced(self):
        ModulePermissions.objects.create(pk=1, wms_narudzbe=False, wms_zalihe=False)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(WMSCustomer.objects.exists())
        url = reverse('staff_wms_section', args=['lokacije'])
        self.assertEqual(self.client.post(url, {'naziv': 'Zabranjena'}).status_code, 403)
        self.assertFalse(WMSLocation.objects.exists())
