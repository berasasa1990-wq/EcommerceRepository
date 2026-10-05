from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import WarehouseCustomer, Product, WarehouseLocation, WarehouseStock, Order
from .views_magacin import _save_warehouse_customer
from .magacin import MagacinError


@override_settings(ALLOWED_HOSTS=['testserver'], EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CheckoutCustomerTests(TestCase):
    def test_phone_formats_reuse_existing_customer_without_overwriting(self):
        customer = WarehouseCustomer.objects.create(ime_prezime='Postojeći kupac', telefon='+387 61 123 456',
                                                    adresa='Sačuvana adresa', odbio_posiljku=True)
        for phone in ('061123456', '061 123 456', '00387 61 123 456', '+38761123456'):
            found = _save_warehouse_customer(ime='Novo ime', telefon=phone, adresa='Druga adresa', update_existing=False)
            self.assertEqual(found.pk, customer.pk)
        customer.refresh_from_db()
        self.assertEqual(customer.ime_prezime, 'Postojeći kupac')
        self.assertEqual(customer.adresa, 'Sačuvana adresa')
        self.assertTrue(customer.odbio_posiljku)
        self.assertEqual(WarehouseCustomer.objects.count(), 1)

    def test_manual_edit_cannot_duplicate_equivalent_phone(self):
        _save_warehouse_customer(ime='Prvi', telefon='061123456')
        other = _save_warehouse_customer(ime='Drugi', telefon='062123456')
        with self.assertRaises(MagacinError):
            _save_warehouse_customer(ime='Drugi', telefon='+387 61 123 456', customer_id=other.pk)
        self.assertEqual(WarehouseCustomer.objects.count(), 2)

    def checkout(self, *, fail=False):
        product = Product.objects.create(naziv='Web artikal', cijena=10, stanje=5, na_stanju=True, magacin_sync_at=timezone.now())
        location, _ = WarehouseLocation.objects.get_or_create(sifra='CUSTOMER-A', defaults={'naziv': 'A'})
        WarehouseStock.objects.create(product=product, location=location, kolicina=5)
        session = self.client.session
        session['cart'] = {f'{product.pk}:0': {'product_id': product.pk, 'variation_id': None,
            'quantity': 1, 'cijena': '10.00', 'bazna_cijena': '10.00', 'na_akciji': False,
            'naziv': product.naziv, 'product_naziv': product.naziv, 'sifra': 'TEST-SKU'}}
        session.save()
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in ('queue_order_emails', 'sync_narudzba', 'track_purchase', 'azuriraj_loyalty_nakon_narudzbe'):
                stack.enter_context(patch('EcommerceApp.views.' + name, return_value=None))
            stack.enter_context(patch('EcommerceApp.staff_alerts.notify_purchase'))
            if fail:
                stack.enter_context(patch('EcommerceApp.magacin.reserve_web_order_stock', side_effect=MagacinError('Nema zalihe')))
            response = self.client.post(reverse('checkout'), {
                'ime_prezime': 'Online kupac', 'telefon': '061123456', 'email': 'kupac@example.com',
                'adresa': 'Ulica 1', 'grad': 'Sarajevo', 'postanski_broj': '71000',
            })
        self.assertEqual(response.status_code, 302)
        return response

    def test_successful_guest_orders_save_one_customer(self):
        self.checkout()
        self.checkout()
        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(WarehouseCustomer.objects.count(), 1)
        customer = WarehouseCustomer.objects.get()
        self.assertEqual((customer.ime_prezime, customer.telefon, customer.adresa), ('Online kupac', '061123456', 'Ulica 1'))
        self.assertFalse(Order.objects.filter(korisnik__isnull=False).exists())

    def test_failed_checkout_does_not_create_customer(self):
        self.checkout(fail=True)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(WarehouseCustomer.objects.exists())


class CheckoutPostalCodeTests(TestCase):
    def test_postal_code_is_required_in_browser_and_server(self):
        from .forms import CheckoutForm
        data = dict(ime_prezime='Test Kupac', telefon='061123456',
                    adresa='Ulica 1', grad='Sarajevo', payment_method='cod')
        self.assertTrue(CheckoutForm.base_fields['postanski_broj'].required)
        self.assertIn('required', str(CheckoutForm()['postanski_broj']))
        for value in (None, '', '   '):
            form = CheckoutForm({**data, 'postanski_broj': value})
            self.assertFalse(form.is_valid())
            self.assertIn('postanski_broj', form.errors)
        form = CheckoutForm({**data, 'postanski_broj': '71000'})
        self.assertTrue(form.is_valid())
