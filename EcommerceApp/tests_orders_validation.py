from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from .models import OnlineGiftCampaign, OnlineGiftClaim, Order
from .views import staff_orders_validation, staff_online_orders


class WebOrdersOnlyTests(TestCase):
    def setUp(self):
        self.web = Order.objects.create(ime_prezime='Web kupac', ukupno=25)
        self.manual = Order.objects.create(ime_prezime='Ručni kupac', ukupno=40, izvor=Order.Izvor.MAGACIN)
        self.transfer = Order.objects.create(ime_prezime='Prenos u MP', ukupno=60,
                                             izvor=Order.Izvor.MAGACIN, pick_state={'kind': 'prenos_mp'})
        self.legacy_transfer = Order.objects.create(ime_prezime='Prenos u MP', ukupno=80)
        self.marked_transfer = Order.objects.create(ime_prezime='MP dokument', ukupno=90, pick_state={'kind': 'prenos_mp'})

    def context(self, view, params=None):
        request = RequestFactory().get('/nalog/narudzbe/', params or {})
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)
        request.resolver_match = SimpleNamespace(url_name='staff_orders_validation')
        with patch('EcommerceApp.views.render', side_effect=lambda r, t, c: c), patch('EcommerceApp.bera_home.render_storefront', side_effect=lambda r, t, c: c), patch('EcommerceApp.views._base_context', return_value={}):
            return view(request)

    def test_list_count_total_and_search_only_include_web_orders(self):
        context = self.context(staff_orders_validation)
        self.assertEqual([o.pk for o in context['orders']], [self.web.pk])
        self.assertEqual(context['pending_count'], 1)
        self.assertEqual(context['pending_total'], Decimal('25'))
        for params in ({'q': 'Prenos'}, {'source': 'magacin'}):
            self.assertEqual(list(self.context(staff_orders_validation, params)['orders']), [])

    def test_legacy_online_list_and_search_exclude_transfers(self):
        context = self.context(staff_online_orders, {'filter': 'sve'})
        self.assertEqual([o.pk for o in context['orders']], [self.web.pk])
        self.assertEqual(context['nova_count'], 1)
        self.assertEqual(self.context(staff_online_orders, {'q': self.transfer.broj})['orders'], [])

    def test_scratch_is_green_only_when_used_on_this_order(self):
        campaign = OnlineGiftCampaign.objects.create(naziv='Test greb')
        claim = OnlineGiftClaim.objects.create(
            campaign=campaign, scratch_tracking_enabled=True,
            scratch_trigger_order=self.web,
        )
        self.assertFalse(list(self.context(staff_orders_validation)['orders'])[0].scratch_used)
        claim.reward_consumed = True
        claim.order = self.manual
        claim.save()
        self.assertFalse(list(self.context(staff_orders_validation)['orders'])[0].scratch_used)
        claim.order = self.web
        claim.save()
        self.assertTrue(list(self.context(staff_orders_validation)['orders'])[0].scratch_used)

    def test_transfer_document_label_including_legacy_records(self):
        for order in (self.transfer, self.legacy_transfer, self.marked_transfer):
            self.assertTrue(order.is_mp_transfer)
            self.assertEqual(order.display_customer_name, 'Prenosnica u MP')
            html = render_to_string('partials/order_invoice_document.html', {'order': order})
            self.assertIn('Prenosnica u MP br.', html)
            self.assertNotIn('Online Narudžbe', html)
        self.assertFalse(self.web.is_mp_transfer)
        self.assertEqual(self.web.display_customer_name, 'Web kupac')


class PanelB2BOrdersTests(TestCase):
    def setUp(self):
        from uuid import uuid4
        from .models import B2BAccount, B2BSubmission
        self.web = Order.objects.create(ime_prezime='Web kupac', ukupno=25)
        self.manual = Order.objects.create(ime_prezime='Ručni kupac', ukupno=40, izvor=Order.Izvor.MAGACIN)
        self.b2b = Order.objects.create(ime_prezime='Firma', ukupno=117, izvor=Order.Izvor.MAGACIN)
        self.account = B2BAccount.objects.create(username='firma', company='Firma', telefon='065123456', email='firma@example.com')
        B2BSubmission.objects.create(account=self.account, order=self.b2b, token=uuid4(), payment='ziralno', netto_total=100)

    def context(self, params=None, panel=True):
        request = RequestFactory().get('/panel/narudzbe/', params or {})
        request.user = SimpleNamespace(is_authenticated=True, is_superuser=True)
        request.resolver_match = SimpleNamespace(url_name='staff_panel_orders' if panel else 'staff_orders_validation')
        with patch('EcommerceApp.bera_home.render_storefront', side_effect=lambda r, t, c: c), patch('EcommerceApp.views._base_context', return_value={}):
            return staff_orders_validation(request)

    def test_panel_includes_b2b_with_company_payment_and_totals(self):
        context = self.context()
        self.assertEqual({o.pk for o in context['orders']}, {self.web.pk, self.b2b.pk})
        self.assertEqual(context['pending_count'], 2)
        self.assertEqual(context['pending_total'], Decimal('142'))
        order = next(o for o in context['orders'] if o.pk == self.b2b.pk)
        self.assertTrue(order.is_b2b_order)
        self.assertEqual(order.panel_customer, 'Firma')
        self.assertEqual(order.panel_phone, self.account.telefon)
        self.assertEqual(order.panel_email, self.account.email)
        self.assertEqual(order.panel_payment, 'Žiralno')
        html = render_to_string('bera_reference/staff/orders_validation_content.html', context)
        self.assertIn('B2B veleprodaja', html)
        self.assertIn('Žiralno', html)
        self.assertIn('firma@example.com', html)

    def test_channel_filters_and_search(self):
        self.assertEqual([o.pk for o in self.context({'source': 'b2b'})['orders']], [self.b2b.pk])
        self.assertEqual([o.pk for o in self.context({'source': 'webshop'})['orders']], [self.web.pk])
        self.assertEqual([o.pk for o in self.context({'q': 'Firma'})['orders']], [self.b2b.pk])
        for query in (self.account.telefon, self.account.email):
            self.assertEqual([o.pk for o in self.context({'q': query})['orders']], [self.b2b.pk])
        self.assertEqual([o.pk for o in self.context(panel=False)['orders']], [self.web.pk])

    def test_b2b_completed_cancelled_and_transfer_exclusions(self):
        self.b2b.status = Order.Status.ZAVRSENA
        self.b2b.save()
        self.assertEqual([o.pk for o in self.context()['orders']], [self.web.pk])
        self.assertEqual([o.pk for o in self.context({'status': 'zavrsene'})['orders']], [self.b2b.pk])
        self.b2b.status = Order.Status.OTKAZANA
        self.b2b.save()
        self.assertEqual([o.pk for o in self.context({'status': 'otkazana'})['orders']], [self.b2b.pk])
        self.b2b.pick_state = {'kind': 'prenos_mp'}
        self.b2b.save()
        self.assertEqual(list(self.context({'status': 'otkazana'})['orders']), [])
