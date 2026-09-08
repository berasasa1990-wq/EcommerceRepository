from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Akcija, Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class AkcijaAdminSaveTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('actions', 'actions@example.com', 'test'))
        self.products = [Product.objects.create(naziv=f'Proizvod {i}', cijena=100, aktivan=True, na_stanju=True) for i in range(2)]
        self.base = {'naziv': 'Nova akcija', 'aktivan': 'on', 'redoslijed': '0'}
        self.display = {'tekst_dugmeta': 'Dodaj u korpu', 'boja_dugmeta': '#666666', 'boja_opisa': '#666666',
                        'za_prijavljene': 'on', 'za_neprijavljene': 'on', 'ponovo_poslije_dana': '0'}
        self.url = reverse('admin:EcommerceApp_akcija_add')

    def save(self, data, obj=None):
        url = reverse('admin:EcommerceApp_akcija_change', args=[obj.pk]) if obj else self.url
        response = self.client.post(url, {**self.base, **data})
        errors = response.context['adminform'].form.errors if response.status_code == 200 else ''
        inline_errors = [f.formset.errors for f in response.context['inline_admin_formsets']] if response.status_code == 200 else ''
        self.assertEqual(response.status_code, 302, (errors, inline_errors))
        return Akcija.objects.get(pk=obj.pk) if obj else Akcija.objects.get(naziv=self.base['naziv'])

    def test_add_and_edit_ponuda_without_ai_fields(self):
        data = {'tip': 'ponuda', 'artikal': self.products[0].pk, 'gratis_artikal': self.products[1].pk, 'popust_postotak': '10,0%'}
        obj = self.save(data)
        from .gratis import build_gratis_offer_response
        self.assertEqual(build_gratis_offer_response(obj)['discounted_price'], '90.00')
        obj = self.save({**data, 'popust_postotak': ''}, obj)
        self.assertFalse(build_gratis_offer_response(obj)['has_discount'])

    def test_quantity_tiers_save_update_and_remove(self):
        data = {**self.display, 'tip': 'qty_deal', 'artikal': self.products[0].pk, 'qty_2_popust': '10', 'qty_3_popust': '12,5%'}
        obj = self.save(data)
        self.assertEqual(list(obj.qty_tiers.values_list('quantity', 'popust_postotak')), [(2, Decimal('10')), (3, Decimal('12.5'))])
        obj = self.save({**data, 'qty_2_popust': '', 'qty_3_popust': '20'}, obj)
        self.assertEqual(list(obj.qty_tiers.values_list('quantity', 'popust_postotak')), [(3, Decimal('20'))])
        self.assertTrue(obj.qty_deal_page_offer())

    def bundle_data(self):
        return {**self.display, 'tip': 'bundle', 'bundle_trigger': 'bundle_product', 'popup_delay_seconds': '5',
                'popust_postotak': '10', 'bundle_lines-TOTAL_FORMS': '1', 'bundle_lines-INITIAL_FORMS': '0',
                'bundle_lines-MIN_NUM_FORMS': '0', 'bundle_lines-MAX_NUM_FORMS': '1000',
                'bundle_lines-0-product': self.products[0].pk, 'bundle_lines-0-quantity': '2', 'bundle_lines-0-redoslijed': '0'}

    def test_bundle_add_and_edit(self):
        data = self.bundle_data()
        obj = self.save(data)
        self.assertEqual(obj.bundle_unit_count(), 2)
        line = obj.bundle_lines.get()
        obj = self.save({**data, 'bundle_lines-INITIAL_FORMS': '1', 'bundle_lines-0-id': line.pk,
                         'bundle_lines-0-quantity': '3'}, obj)
        self.assertEqual(obj.bundle_unit_count(), 3)
        from .views import _product_page_bundle
        self.assertIsNotNone(_product_page_bundle(self.products[0]))
        obj.bundle_trigger = 'trigger_product'
        obj.artikal = self.products[1]
        obj.save()
        self.assertIsNotNone(_product_page_bundle(self.products[1]))

    def test_switch_existing_ponuda_to_bundle(self):
        obj = self.save({'tip': 'ponuda', 'artikal': self.products[0].pk, 'gratis_artikal': self.products[1].pk})
        response = self.client.get(reverse('admin:EcommerceApp_akcija_change', args=[obj.pk]))
        for name in ['bundle_lines-TOTAL_FORMS', 'id_qty_2_popust', 'id_browse_interest_mode']:
            self.assertContains(response, name)
        obj = self.save(self.bundle_data(), obj)
        self.assertEqual(obj.tip, 'bundle')
        self.assertEqual(obj.bundle_unit_count(), 2)

    def test_invalid_inputs_show_relevant_errors(self):
        for data, field in [({'tip': 'ponuda'}, 'gratis_artikal'),
                            ({**self.display, 'tip': 'qty_deal', 'artikal': self.products[0].pk}, 'qty_2_popust')]:
            response = self.client.post(self.url, {**self.base, **data})
            self.assertEqual(response.status_code, 200)
            errors = response.context['adminform'].form.errors
            self.assertIn(field, errors)
            self.assertNotIn('browse_interest_mode', errors)
            self.assertNotIn('browse_interest_source', errors)

    def test_flash_offer_saves_its_own_items(self):
        obj = self.save({'tip': 'akcijska', 'trajanje_sati': '24',
                         'popust_postotak': '15', 'flash_lines-TOTAL_FORMS': '1', 'flash_lines-INITIAL_FORMS': '0',
                         'flash_lines-MIN_NUM_FORMS': '0', 'flash_lines-MAX_NUM_FORMS': '4',
                         'flash_lines-0-product': self.products[0].pk, 'flash_lines-0-redoslijed': '0'})
        self.assertEqual(obj.flash_lines.count(), 1)
        response = self.client.get(reverse('admin:EcommerceApp_akcija_change', args=[obj.pk]))
        self.assertNotContains(response, 'id_flash_trigger')
        self.assertIsNotNone(obj.pocetak)
        self.assertTrue(obj.flash_applies_to_product(self.products[0]))

    def test_bundle_rejects_zero_quantity_and_excessive_discount(self):
        for field, value in [('bundle_lines-0-quantity', '0'), ('bundle_lines-0-popust_postotak', '101')]:
            response = self.client.post(self.url, {**self.base, **self.bundle_data(), field: value})
            self.assertEqual(response.status_code, 200)
            formset = response.context['inline_admin_formsets'][0].formset
            self.assertTrue(formset.errors[0])
        self.assertFalse(Akcija.objects.filter(naziv=self.base['naziv']).exists())
