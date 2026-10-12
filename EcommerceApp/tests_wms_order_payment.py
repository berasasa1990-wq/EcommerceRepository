import json
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Product, WMSOrder


class WMSOrderPaymentTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('payment-wms', 'payment@example.test', 'test-only'))
        self.product = Product.objects.create(naziv='Artikal', sifra='PAY', cijena=20, stanje=5)
        p = patch('EcommerceApp.views_wms.module_locked', return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def create(self, tip, payment=None):
        payload = {'kupac': 'Kupac', 'napomena': 'Sačuvaj napomenu',
                   'items': json.dumps([{'id': self.product.pk, 'quantity': 2}])}
        if payment is not None:
            payload['payment'] = payment
        return self.client.post(reverse('staff_wms_create_order', args=[tip]), payload)

    def test_wholesale_cash_and_bank_transfer(self):
        for value, label in [('gotovina', 'Gotovinski'), ('ziralno', 'Žiralno')]:
            with self.subTest(payment=value):
                self.assertEqual(self.create('vp', value).status_code, 302)
                order = WMSOrder.objects.latest('pk')
                self.assertEqual(order.payment_label, label)
                self.assertIn('Sačuvaj napomenu', order.napomena)
                self.assertEqual(order.items.get().kolicina, 2)

    def test_wholesale_requires_valid_choice_without_creating_order(self):
        for payment in (None, '', 'kartica', 'pogresno'):
            response = self.create('vp', payment)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Izaberite način plaćanja')
            self.assertFalse(WMSOrder.objects.exists())

    def test_online_always_cash_even_if_bank_transfer_is_posted(self):
        for payment in (None, 'ziralno', 'kartica'):
            self.assertEqual(self.create('online', payment).status_code, 302)
            self.assertEqual(WMSOrder.objects.latest('pk').payment_label, 'Gotovinski')

    def test_payment_dialog_only_for_wholesale(self):
        wholesale = self.client.get(reverse('staff_wms_create_order', args=['vp']))
        online = self.client.get(reverse('staff_wms_create_order', args=['online']))
        self.assertContains(wholesale, 'id="orderPaymentDialog"')
        self.assertContains(wholesale, 'data-order-payment="ziralno"')
        self.assertNotContains(online, 'id="orderPaymentDialog"')

    def test_legacy_wholesale_payment_is_not_assumed(self):
        order = WMSOrder(tip='vp', napomena='Bez podatka o plaćanju')
        self.assertEqual(order.payment_label, 'Nije evidentirano')

    def test_unavailable_product_cannot_create_online_or_wholesale_order(self):
        self.product.stanje = 0
        self.product.save(update_fields=['stanje'])
        for tip in ('vp', 'online'):
            response = self.create(tip, 'gotovina')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'nije dostupan')
            self.assertFalse(WMSOrder.objects.exists())

    def test_reserved_stock_is_not_available(self):
        from .models import WarehouseLocation, WarehouseStock
        location = WarehouseLocation.objects.create(sifra='RES-PAY', naziv='Rezervisano')
        WarehouseStock.objects.create(product=self.product, location=location, kolicina=5, rezervisano=5)
        self.assertContains(self.create('online'), 'nije dostupan')
        self.assertFalse(WMSOrder.objects.exists())

    def test_wholesale_draft_cannot_bypass_zero_stock_with_mp_flag(self):
        from types import SimpleNamespace
        from .magacin import add_vp_stavka, MagacinError
        from .models import MagacinVpNarudzba
        draft = SimpleNamespace(status=MagacinVpNarudzba.Status.U_TOKU)
        with patch('EcommerceApp.magacin.display_stock_totals', return_value={'dostupno': 0}):
            with self.assertRaisesMessage(MagacinError, 'nije dostupan'):
                add_vp_stavka(draft, product=self.product, qty=1, mp_ok=True)

    def test_wholesale_completion_rechecks_current_stock_even_with_mp_flag(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from .magacin import finish_vp_narudzba, MagacinError
        from .models import MagacinVpNarudzba
        row = SimpleNamespace(product_id=self.product.pk, product=self.product, variation=None,
                              naziv=self.product.naziv, kolicina=1, mp_ok=True)
        draft = SimpleNamespace(status=MagacinVpNarudzba.Status.U_TOKU, ime_prezime='Kupac', telefon='061111111',
                                stavke=Mock())
        draft.stavke.select_related.return_value = [row]
        with patch('EcommerceApp.magacin.display_stock_totals', return_value={'dostupno': 0}):
            with self.assertRaisesMessage(MagacinError, 'nije dostupan'):
                finish_vp_narudzba(draft, placanje='gotovina')
