from uuid import uuid4
from django.template.loader import render_to_string
from django.test import TestCase
from .models import B2BAccount, B2BSubmission, CardPayment, Order, WMSOrder
from .emails import _admin_order_text


class OrderMetadataTests(TestCase):
    def b2b_order(self, payment):
        account = B2BAccount.objects.create(username=str(uuid4()), company='B2B firma')
        order = Order.objects.create(ime_prezime='B2B firma', izvor=Order.Izvor.MAGACIN, ukupno=117)
        B2BSubmission.objects.create(account=account, order=order, token=uuid4(), payment=payment, netto_total=100)
        return order

    def test_b2b_metadata_and_payment_on_documents_and_email(self):
        for payment, label in [('gotovina', 'Gotovinski'), ('ziralno', 'Žiralno')]:
            order = self.b2b_order(payment)
            self.assertTrue(order.is_b2b_order)
            self.assertEqual(order.payment_label, label)
            self.assertEqual(order.order_source_label, 'B2B veleprodaja')
            # The submission is authoritative even if older discount metadata is absent.
            self.assertEqual(order.packing_placanje_label(), label.upper())
            for template in ['partials/order_metadata.html', 'partials/order_invoice_document.html', 'bera_reference/partials/order_invoice_document.html', 'partials/order_packing_document.html', 'bera_reference/partials/order_packing_document.html']:
                html = render_to_string(template, {'order': order, 'metadata_order': order})
                self.assertIn('B2B veleprodaja', html, template)
                self.assertIn(label, html, template)
                self.assertNotIn('Način plaćanja: pouzećem', html)
            text = _admin_order_text(order)
            self.assertIn('B2B veleprodaja', text)
            self.assertIn(label, text)

    def test_cash_bank_and_card_including_pending_payment(self):
        cash = Order.objects.create(ime_prezime='Web kupac', ukupno=10)
        bank = Order.objects.create(ime_prezime='Žiralni kupac', ukupno=10, izvor=Order.Izvor.MAGACIN, popust_detalji=[{'placanje': 'ziralno'}])
        legacy = Order.objects.create(ime_prezime='Stari kupac', ukupno=10, izvor=Order.Izvor.MAGACIN, napomena='Plaćanje: žiralno')
        self.assertFalse(cash.is_b2b_order)
        self.assertEqual(cash.payment_label, 'Gotovinski')
        self.assertEqual(bank.payment_label, 'Žiralno')
        self.assertEqual(legacy.payment_label, 'Žiralno')
        payment = CardPayment.objects.create(order=cash, amount=1000, status='pending')
        self.assertEqual(cash.payment_label, 'Karticom — čeka uplatu')
        self.assertFalse(cash.placeno_karticom())
        payment.status = 'paid'
        payment.save()
        cash.refresh_from_db()
        self.assertEqual(cash.payment_label, 'Karticom')
        self.assertTrue(cash.placeno_karticom())

    def test_wms_forwards_source_order_metadata(self):
        order = self.b2b_order('ziralno')
        wms = WMSOrder.objects.create(source_order=order, kupac='B2B firma', tip='vp')
        self.assertTrue(wms.is_b2b_order)
        self.assertEqual(wms.order_source_label, 'B2B veleprodaja')
        self.assertEqual(wms.payment_label, 'Žiralno')
        html = render_to_string('partials/order_metadata.html', {'metadata_order': wms})
        self.assertIn('B2B veleprodaja', html)
        self.assertIn('Žiralno', html)
        independent = WMSOrder.objects.create(kupac='Nepovezana firma', tip='vp')
        self.assertFalse(independent.is_b2b_order)
        self.assertEqual(independent.payment_label, 'Nije evidentirano')
