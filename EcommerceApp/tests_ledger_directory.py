from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Order, WarehouseCustomer, WarehousePartner
from .ledger_orders import customer_orders
from .warehouse_customers_ledger import ensure_order_partners


@override_settings(SECURE_SSL_REDIRECT=False, SITE_PREP_ENABLED=False)
class LedgerDirectoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('ledger-admin', 'staff@example.com', 'pass')
        self.client.force_login(self.user)
        self.url = reverse('staff_magacin_duguje')

    def order(self, number, name='Historijski Kupac', **kwargs):
        return Order.objects.create(broj=number, ime_prezime=name, telefon='061123456',
                                    email='historic@example.com', ukupno=20, **kwargs)

    def test_historical_customers_without_entries_are_visible_and_orders_readable(self):
        first = self.order('OLD-WEB')
        second = self.order('OLD-MANUAL', name='Novo ime kupca')
        before = list(Order.objects.values())
        ensure_order_partners()
        ensure_order_partners()
        self.assertEqual(WarehouseCustomer.objects.count(), 1)
        partner = WarehousePartner.objects.get()
        self.assertEqual(set(customer_orders(partner)), {first, second})
        page = self.client.get(self.url, {'partner': partner.pk, 'tab': 'orders'})
        self.assertContains(page, 'OLD-WEB')
        self.assertContains(page, 'OLD-MANUAL')
        self.assertEqual(page.context['return_orders'].count(), 2)
        self.assertEqual(page.context['partners'].paginator.count, 1)
        self.assertEqual(list(Order.objects.values()), before)
        self.assertFalse(partner.entries.exists())

    def test_lookup_does_not_hide_orders_after_fifty(self):
        for number in range(52):
            self.order(f'HISTORY-{number}')
        ensure_order_partners()
        partner = WarehousePartner.objects.get()
        response = self.client.get(self.url, {'partner': partner.pk, 'orders_lookup': '1'})
        self.assertEqual(len(response.json()['orders']), 52)

    def test_internal_transfer_does_not_become_customer(self):
        self.order('INTERNAL', name='Prenos u MP')
        ensure_order_partners()
        self.assertFalse(WarehousePartner.objects.exists())

    def test_existing_customer_details_and_balance_are_preserved(self):
        customer = WarehouseCustomer.objects.create(ime_prezime='Sačuvano ime', telefon='061123456',
                                                     email='historic@example.com', adresa='Sačuvana adresa')
        before = WarehouseCustomer.objects.values().get(pk=customer.pk)
        order = self.order('MATCHED')
        ensure_order_partners()
        self.assertEqual(WarehouseCustomer.objects.values().get(pk=customer.pk), before)
        self.assertEqual(list(customer_orders(customer.ledger_partner)), [order])

    def test_empty_directory_page_renders(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_explicit_vp_and_b2b_web_orders_share_partner_without_contact_renaming(self):
        from .models import MagacinVpNarudzba
        customer = WarehouseCustomer.objects.create(ime_prezime='Kupac firma', telefon='061123456', email='historic@example.com')
        vp = self.order('VP-ORDER', name='VP snapshot', izvor=Order.Izvor.MAGACIN)
        MagacinVpNarudzba.objects.create(customer=customer, order=vp)
        web = self.order('WEB-ORDER')
        b2b = self.order('B2B-ORDER', name='Firma B2B')
        ensure_order_partners()
        partner = customer.ledger_partner
        self.assertEqual(set(customer_orders(partner)), {vp, web, b2b})
        response = self.client.get(self.url, {'partner': partner.pk, 'orders_lookup': '1'})
        self.assertEqual({o['number'] for o in response.json()['orders']}, {'VP-ORDER', 'WEB-ORDER', 'B2B-ORDER'})
