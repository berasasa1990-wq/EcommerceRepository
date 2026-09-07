from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse

from .magacin import MagacinError
from .models import (WarehousePartner, WarehouseLedgerEntry as Entry, WarehouseStock,
                     WarehouseLocation, WarehouseMovement, Product, ProductVariation, Order,
                     OrderItem, MagacinPlan, MagacinSubscription)
from .warehouse_ledger import post_entry


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class WarehouseLedgerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='ledger-user', password='test')
        plan, _ = MagacinPlan.objects.get_or_create(code='ultimate', defaults={'name': 'Ultimate', 'features': []})
        MagacinSubscription.objects.update_or_create(user=self.user, defaults={'plan': plan, 'active': True})
        self.partner = WarehousePartner.objects.create(naziv='Partner <test>', grad='Sarajevo')
        self.product = Product.objects.create(naziv='Test artikal', sifra='LEDGER-1', cijena=Decimal('10'))
        self.location = WarehouseLocation.objects.create(sifra='LEDGER-A', naziv='Lager A')
        self.stock = WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=10)

    def post(self, **data):
        payload = {'action': 'entry', 'kind': 'debit', 'amount': '10', 'token': str(uuid4()), **data}
        return post_entry(partner_id=self.partner.pk, data=payload, user=self.user)

    def goods(self, **extra):
        return self.post(with_goods='1', product_id=str(self.product.pk), location_id=str(self.location.pk), quantity='3', price='10', **extra)

    def balance(self):
        return self.partner.entries.aggregate(total=Sum('amount'))['total'] or Decimal('0')

    def test_goods_and_partial_returns_are_atomic_and_audited(self):
        entry = self.goods()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 7)
        self.assertEqual(self.balance(), Decimal('30'))
        line = entry.lines.get()
        self.post(action='return', line_id=str(line.pk), location_id=str(self.location.pk), quantity='2')
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 9)
        self.assertEqual(self.balance(), Decimal('10'))
        self.assertEqual(WarehouseMovement.objects.count(), 2)
        with self.assertRaises(MagacinError):
            self.post(action='return', line_id=str(line.pk), location_id=str(self.location.pk), quantity='2')
        self.assertEqual(Entry.objects.count(), 2)
        self.assertEqual(WarehouseMovement.objects.count(), 2)

    def test_supplier_goods_return_and_both_payment_directions(self):
        entry = self.goods(kind='credit')
        self.assertEqual(self.balance(), Decimal('-30'))
        self.post(action='return', line_id=str(entry.lines.get().pk), location_id=str(self.location.pk), quantity='1')
        self.assertEqual(self.balance(), Decimal('-20'))
        self.post(kind='payment', amount='20')
        self.assertEqual(self.balance(), 0)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 12)
        self.post(kind='debit', amount='50')
        self.post(kind='receipt', amount='50')
        self.assertEqual(self.balance(), 0)

    def test_duplicate_submission_moves_stock_once(self):
        token = str(uuid4())
        first = self.goods(token=token)
        second = self.goods(token=token)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WarehouseMovement.objects.count(), 1)
        self.assertEqual(Entry.objects.count(), 1)

    def test_insufficient_stock_rolls_back_ledger(self):
        self.stock.rezervisano = 9
        self.stock.save()
        with self.assertRaises(MagacinError):
            self.goods()
        self.assertFalse(Entry.objects.exists())
        self.assertFalse(WarehouseMovement.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)

    def test_invalid_money_and_quantities(self):
        for amount in ['NaN', 'Infinity', '-1', '0', '1.001', '1e999999', 'abc']:
            with self.subTest(amount=amount), self.assertRaises(MagacinError):
                self.post(amount=amount)
        for qty in ['0', '-1', '1.5', '1000001']:
            with self.subTest(qty=qty), self.assertRaises(MagacinError):
                self.post(with_goods='1', product_id=self.product.pk, location_id=self.location.pk, quantity=qty, price='10')
        self.assertFalse(Entry.objects.exists())

    def test_variation_required_and_foreign_return_rejected(self):
        ProductVariation.objects.create(artikal=self.product, naziv='Var A')
        with self.assertRaises(MagacinError):
            self.goods()
        other = WarehousePartner.objects.create(naziv='Drugi')
        entry = self.post()
        from .models import WarehouseLedgerLine
        line = WarehouseLedgerLine.objects.create(entry=entry, product=self.product, name='Test', quantity=1, amount=10)
        with self.assertRaises(MagacinError):
            post_entry(partner_id=other.pk, user=self.user, data={'token':str(uuid4()), 'action':'return', 'line_id':line.pk, 'location_id':self.location.pk, 'quantity':'1'})

    def order(self, **extra):
        return Order.objects.create(broj='LEDGER-ORDER', ime_prezime='Partner', email='p@example.com', telefon='1', adresa='A', grad='Sarajevo', ukupno=Decimal('27'), dostava=Decimal('3'), popust=Decimal('6'), lager_status=Order.LagerStatus.VALIDIRANO, **extra)

    def test_order_import_once_without_second_stock_deduction(self):
        order = self.order()
        OrderItem.objects.create(narudzba=order, artikal=self.product, naziv='Test', cijena=10, kolicina=5, kolicina_pokupljeno=3)
        entry = self.post(action='order', order_number=order.broj)
        self.assertEqual(entry.amount, Decimal('27'))
        self.assertEqual(entry.lines.get().quantity, 3)
        self.assertEqual(entry.lines.get().amount, Decimal('24'))
        self.assertFalse(WarehouseMovement.objects.exists())
        with self.assertRaises(MagacinError):
            self.post(action='order', order_number=order.broj)
        self.post(action='return', line_id=entry.lines.get().pk, location_id=self.location.pk, quantity='3')
        self.assertEqual(self.balance(), Decimal('3'))  # Delivery remains on the invoice.
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 13)

    def test_unvalidated_or_cancelled_order_rejected(self):
        order = self.order()
        order.lager_status = Order.LagerStatus.NIJE
        order.save()
        with self.assertRaises(MagacinError):
            self.post(action='order', order_number=order.broj)
        order.lager_status = Order.LagerStatus.VALIDIRANO
        order.status = Order.Status.OTKAZANA
        order.save()
        with self.assertRaises(MagacinError):
            self.post(action='order', order_number=order.broj)

    def test_partial_returns_do_not_over_refund_rounding(self):
        entry = self.goods()
        line = entry.lines.get()
        line.amount = Decimal('.02')
        line.quantity = 4
        line.save()
        returned = []
        for _ in range(4):
            returned.append(self.post(action='return', line_id=line.pk, location_id=self.location.pk, quantity='1').amount)
        self.assertEqual(sum(returned), Decimal('-.02'))
        self.assertTrue(all(value <= 0 for value in returned))

    def test_page_forms_export_and_plan_access(self):
        self.client.force_login(self.user)
        url = reverse('staff_magacin_duguje')
        self.assertContains(self.client.get(url), 'Duguje / Potražuje')
        entry = self.goods()
        response = self.client.get(url, {'partner': self.partner.pk})
        self.assertContains(response, 'Test artikal')
        self.assertContains(response, 'Partner &lt;test&gt;')
        self.assertEqual(self.client.get(url, {'export':1, 'partner':self.partner.pk})['Content-Type'], 'text/csv; charset=utf-8')
        response = self.client.post(url, {'action':'entry', 'kind':'receipt', 'amount':'5', 'partner_id':self.partner.pk, 'token':str(uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.balance(), Decimal('25'))
        basic = MagacinPlan.objects.get(code='basic')
        MagacinSubscription.objects.filter(user=self.user).update(plan=basic)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'action':'partner', 'naziv':'Blocked'}).status_code, 403)
        self.assertFalse(WarehousePartner.objects.filter(naziv='Blocked').exists())

    def test_multi_article_booking_and_rollback(self):
        import json
        row = {'product_id': self.product.pk, 'location_id': self.location.pk, 'quantity': '2', 'price': '10'}
        entry = self.post(with_goods='1', items_json=json.dumps([row, row]))
        self.assertEqual(entry.lines.count(), 2)
        self.assertEqual(entry.amount, Decimal('40'))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 6)
        with self.assertRaises(MagacinError):
            self.post(with_goods='1', items_json=json.dumps([row, {**row, 'quantity': '9'}]))
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 6)
        self.assertEqual(Entry.objects.count(), 1)
        self.assertEqual(WarehouseMovement.objects.count(), 2)
        with self.assertRaises(MagacinError):
            self.post(with_goods='1', items_json='[]')

    def test_customer_directory_sync_preserves_ledger(self):
        from .models import WarehouseCustomer
        customer = WarehouseCustomer.objects.create(ime_prezime='Kupac iz baze', telefon='061222333', grad='Tuzla')
        partner = customer.ledger_partner
        Entry.objects.create(partner=partner, kind='debit', amount=25, description='Dug', user=self.user, token=uuid4())
        partner.napomena = 'Sačuvaj napomenu'
        partner.save()
        customer.ime_prezime = 'Novo ime'
        customer.adresa = 'Nova adresa 1'
        customer.save(update_fields=['ime_prezime', 'adresa'])
        partner.refresh_from_db()
        self.assertEqual(partner.naziv, 'Novo ime')
        self.assertEqual(partner.adresa, 'Nova adresa 1')
        self.assertEqual(partner.napomena, 'Sačuvaj napomenu')
        self.assertEqual(partner.entries.get().amount, Decimal('25'))
        self.assertEqual(WarehousePartner.objects.filter(customer=customer).count(), 1)
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'q':'061222333'})
        self.assertContains(response, 'Novo ime')

    def test_new_ledger_partner_is_saved_in_customer_directory(self):
        from .models import WarehouseCustomer
        self.client.force_login(self.user)
        response = self.client.post(reverse('staff_magacin_duguje'), {'action':'partner', 'naziv':'Zajednički kupac', 'telefon':'061999', 'grad':'Mostar'})
        self.assertEqual(response.status_code, 302)
        customer = WarehouseCustomer.objects.get(ime_prezime='Zajednički kupac')
        self.assertEqual(customer.ledger_partner.telefon, '061999')

    def test_deleted_customer_keeps_financial_history(self):
        from .models import WarehouseCustomer
        customer = WarehouseCustomer.objects.create(ime_prezime='Arhiva', telefon='061000')
        partner = customer.ledger_partner
        Entry.objects.create(partner=partner, kind='debit', amount=15, description='Dug', token=uuid4())
        customer.delete()
        partner.refresh_from_db()
        self.assertIsNone(partner.customer_id)
        self.assertEqual(partner.entries.get().amount, Decimal('15'))

    def linked_order(self, **extra):
        from .models import WarehouseCustomer
        customer = WarehouseCustomer.objects.create(ime_prezime='Kupac narudžbe', telefon='061 222-333')
        self.partner = customer.ledger_partner
        order = Order.objects.create(broj='CUSTOMER-1', ime_prezime=customer.ime_prezime,
                                     telefon='+38761222333', email='x@example.com', adresa='A', grad='Tuzla',
                                     ukupno=30, **extra)
        item = OrderItem.objects.create(narudzba=order, artikal=self.product, naziv='Test artikal', cijena=10, kolicina=3)
        return order, item

    def test_customer_orders_include_cancelled_and_validated_only_for_selected_customer(self):
        from .ledger_orders import customer_orders
        order, item = self.linked_order(status=Order.Status.OTKAZANA)
        self.assertEqual(list(customer_orders(self.partner)), [order])
        other = Order.objects.create(broj='OTHER', ime_prezime='Neko drugi', telefon=order.telefon, ukupno=10)
        self.assertNotIn(other, customer_orders(self.partner))
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk, 'tab':'orders'})
        self.assertContains(response, '#CUSTOMER-1')
        self.assertContains(response, 'Otkazana')
        self.assertNotContains(response, '#OTHER')
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk, 'orders_lookup':'1', 'order_id':other.pk})
        self.assertEqual(response.status_code, 404)

    def test_missing_order_items_record_obligation_without_stock_movement(self):
        import json
        order, item = self.linked_order()
        data = {'action':'missing', 'order_id':order.pk, 'missing_json':json.dumps([{'item_id':item.pk, 'quantity':'2'}])}
        entry = self.post(**data)
        self.assertEqual(entry.amount, Decimal('-20'))
        self.assertEqual(entry.source_order, order)
        self.assertEqual(entry.lines.get().order_item, item)
        self.assertFalse(WarehouseMovement.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        with self.assertRaises(MagacinError):
            self.post(**data)
        with self.assertRaises(MagacinError):
            self.post(action='return', line_id=entry.lines.get().pk, location_id=self.location.pk, quantity='1')
        self.assertEqual(Entry.objects.count(), 1)

    def test_missing_items_reject_other_customer_item_and_cancelled_order(self):
        import json
        order, item = self.linked_order()
        other = Order.objects.create(broj='OTHER', ime_prezime='Neko drugi', telefon='000', ukupno=10)
        wrong = OrderItem.objects.create(narudzba=other, artikal=self.product, naziv='Drugi', cijena=10, kolicina=1)
        with self.assertRaises(MagacinError):
            self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':wrong.pk, 'quantity':'1'}]))
        with self.assertRaises(MagacinError):
            self.post(action='missing', order_id=other.pk, missing_json=json.dumps([{'item_id':wrong.pk, 'quantity':'1'}]))
        order.status = Order.Status.OTKAZANA
        order.save()
        with self.assertRaises(MagacinError):
            self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'1'}]))
        self.assertFalse(Entry.objects.exists())

    def test_excess_sent_records_customer_debt_once_without_stock_change(self):
        import json
        order, item = self.linked_order()
        token = str(uuid4())
        data = {'action':'excess', 'order_id':order.pk, 'missing_json':json.dumps([{'item_id':item.pk, 'quantity':'5'}]), 'token':token}
        entry = self.post(**data)
        self.assertEqual(entry.amount, Decimal('50'))
        self.assertEqual(entry.kind, Entry.Kind.EXCESS)
        self.assertEqual(entry.lines.get().quantity, 5)
        self.assertEqual(self.post(**data).pk, entry.pk)
        self.assertFalse(WarehouseMovement.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        with self.assertRaises(MagacinError):
            self.post(action='return', line_id=entry.lines.get().pk, location_id=self.location.pk, quantity='1')
        # Excess quantities must not consume the missing-item allowance.
        missing = self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'3'}]))
        self.assertEqual(missing.amount, Decimal('-30'))

    def test_order_articles_button_replaces_invoice_action(self):
        order, item = self.linked_order(status=Order.Status.OTKAZANA)
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk, 'tab':'orders'})
        self.assertContains(response, '>Artikli</button>')
        self.assertNotContains(response, '>Faktura</a>')
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk, 'orders_lookup':'1', 'order_id':order.pk})
        self.assertTrue(response.json()['cancelled'])
        self.assertEqual(response.json()['items'][0]['id'], item.pk)

    def test_damaged_article_records_customer_obligation_and_preserves_stock(self):
        import json
        order, item = self.linked_order()
        token = str(uuid4())
        data = {'action':'damaged', 'order_id':order.pk, 'missing_json':json.dumps([{'item_id':item.pk, 'quantity':'2'}]), 'token':token}
        entry = self.post(**data)
        self.assertEqual(entry.kind, Entry.Kind.DAMAGED)
        self.assertEqual(entry.amount, Decimal('-20'))
        self.assertEqual(entry.source_order, order)
        self.assertEqual(entry.lines.get().quantity, 2)
        self.assertEqual(self.post(**data).pk, entry.pk)
        self.assertFalse(WarehouseMovement.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        with self.assertRaises(MagacinError):
            self.post(action='damaged', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'2'}]))
        with self.assertRaises(MagacinError):
            self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'2'}]))
        with self.assertRaises(MagacinError):
            self.post(action='return', line_id=entry.lines.get().pk, location_id=self.location.pk, quantity='1')
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk})
        self.assertContains(response, 'Oštećen artikal — mi dugujemo kupcu')
        self.assertContains(response, 'id="ldDamagedOpen"')
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk, 'orders_lookup':'1', 'order_id':order.pk})
        self.assertEqual(response.json()['items'][0]['remaining'], 1)

    def damaged_line(self):
        import json
        order, item = self.linked_order()
        entry = self.post(action='damaged', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'2'}]))
        return entry.lines.get()

    def test_replacement_creates_free_order_and_reserves_stock_once(self):
        from .warehouse_ledger import create_replacement
        from .ledger_orders import customer_orders
        line = self.damaged_line()
        order = create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)
        self.assertEqual(order.ukupno, 0)
        self.assertEqual(order.dostava, 0)
        self.assertEqual(order.ime_prezime, self.partner.naziv)
        self.assertEqual(order.lager_status, Order.LagerStatus.REZERVISANO)
        self.assertEqual(order.stavke.get().kolicina, 2)
        self.assertEqual(order.stavke.get().artikal, self.product)
        self.assertEqual(order.magacin_holds.get().kolicina, 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        self.assertEqual(self.stock.rezervisano, 2)
        self.assertEqual(create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user).pk, order.pk)
        self.assertIn(order, customer_orders(self.partner))
        self.assertEqual(self.balance(), Decimal('-20'))
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk}), 'Zamjena #' + order.broj)

    def test_replacement_short_stock_rolls_back_partial_reservation_and_order(self):
        from .warehouse_ledger import create_replacement
        line = self.damaged_line()
        self.stock.kolicina = 1
        self.stock.save()
        count = Order.objects.count()
        with self.assertRaises(MagacinError):
            create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)
        self.assertEqual(Order.objects.count(), count)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 0)
        line.refresh_from_db()
        self.assertIsNone(line.replacement_order_id)

    def test_replacement_rejects_other_customer_line(self):
        from .warehouse_ledger import create_replacement
        line = self.damaged_line()
        other = WarehousePartner.objects.create(naziv='Drugi kupac')
        with self.assertRaises(MagacinError):
            create_replacement(partner_id=other.pk, line_id=line.pk, user=self.user)

    def test_validating_replacement_settles_only_its_damaged_debt_once(self):
        from .warehouse_ledger import create_replacement
        from .magacin import validate_order_stock
        line = self.damaged_line()
        self.post(kind='credit', amount='7')
        order = create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)
        self.assertEqual(self.balance(), Decimal('-27'))
        validate_order_stock(order, user=self.user)
        self.assertEqual(self.balance(), Decimal('-7'))
        settlement = Entry.objects.get(kind=Entry.Kind.SETTLED)
        self.assertEqual(settlement.amount, Decimal('20'))
        self.assertEqual(settlement.source_line_id, line.pk)
        self.assertEqual(settlement.user, self.user)
        validate_order_stock(order, user=self.user)
        self.assertEqual(Entry.objects.filter(kind=Entry.Kind.SETTLED).count(), 1)
        self.assertEqual(self.balance(), Decimal('-7'))
        self.assertTrue(Entry.objects.filter(kind=Entry.Kind.DAMAGED).exists())
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner': self.partner.pk})
        self.assertEqual(response.context['lines'].paginator.count, 0)
        self.assertIn(settlement, response.context['entries'])

    def test_unvalidated_and_cancelled_replacements_do_not_settle(self):
        from .warehouse_ledger import create_replacement, settle_replacement
        from .magacin import cancel_order_stock
        line = self.damaged_line()
        order = create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)
        self.assertIsNone(settle_replacement(order))
        cancel_order_stock(order, user=self.user)
        self.assertIsNone(settle_replacement(order))
        self.assertEqual(self.balance(), Decimal('-20'))

    def test_full_payment_archives_debt_items_partial_payment_keeps_them(self):
        line = self.damaged_line()
        self.post(kind='payment', amount='5')
        line.refresh_from_db()
        self.assertIsNone(line.settled_by_id)
        payment = self.post(kind='payment', amount='15')
        self.assertEqual(self.balance(), 0)
        line.refresh_from_db()
        self.assertEqual(line.settled_by, payment)
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk})
        self.assertEqual(response.context['lines'].paginator.count, 0)
        self.assertTrue(Entry.objects.filter(pk=line.entry_id).exists())
        # New debt starts a new active cycle without restoring old articles.
        fresh = self.goods(kind='credit')
        self.assertIsNone(fresh.lines.get().settled_by_id)
        line.refresh_from_db()
        self.assertEqual(line.settled_by, payment)

    def test_paid_damaged_line_cannot_create_replacement_or_credit_twice(self):
        from .warehouse_ledger import create_replacement
        from .magacin import validate_order_stock
        line = self.damaged_line()
        order = create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)
        self.post(kind='payment', amount='20')
        validate_order_stock(order, user=self.user)
        self.assertEqual(self.balance(), 0)
        self.assertFalse(Entry.objects.filter(kind=Entry.Kind.SETTLED).exists())
        with self.assertRaises(MagacinError):
            create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)

    def test_customer_payment_closes_items_and_overpayment_is_preserved(self):
        entry = self.goods()
        payment = self.post(kind='receipt', amount='35')
        self.assertEqual(self.balance(), Decimal('-5'))
        self.assertEqual(entry.lines.get().settled_by, payment)

    def test_missing_article_can_be_sent_without_cash_on_delivery(self):
        import json
        from .warehouse_ledger import create_replacement
        from .xexpress_service import _shipment_amounts
        from .magacin import validate_order_stock
        order, item = self.linked_order()
        entry = self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'2'}]))
        line = entry.lines.get()
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk})
        self.assertContains(response, 'Pošalji artikal')
        self.assertTrue(response.context['lines'][0].can_send)
        shipment = create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)
        self.assertEqual(shipment.stavke.get().kolicina, 2)
        self.assertEqual(_shipment_amounts(shipment)[1:], (False, 0.0))
        self.assertEqual(create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user), shipment)
        validate_order_stock(shipment, user=self.user)
        self.assertEqual(self.balance(), 0)

    def test_missing_article_send_disabled_when_locations_lack_available_quantity(self):
        import json
        from .warehouse_ledger import create_replacement
        order, item = self.linked_order()
        entry = self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'2'}]))
        self.stock.rezervisano = 9
        self.stock.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk})
        self.assertContains(response, 'disabled title="Nema na stanju"')
        self.assertFalse(response.context['lines'][0].can_send)
        count = Order.objects.count()
        with self.assertRaises(MagacinError):
            create_replacement(partner_id=self.partner.pk, line_id=entry.lines.get().pk, user=self.user)
        self.assertEqual(Order.objects.count(), count)

    def test_delete_wrong_item_reverses_debt_and_allows_correct_reentry(self):
        import json
        order, item = self.linked_order()
        entry = self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'2'}]))
        line = entry.lines.get()
        reversal = self.post(action='delete_line', line_id=line.pk)
        self.assertEqual(self.balance(), 0)
        line.refresh_from_db()
        self.assertEqual(line.voided_by, reversal)
        self.assertEqual(self.post(action='delete_line', line_id=line.pk).pk, reversal.pk)
        self.post(action='missing', order_id=order.pk, missing_json=json.dumps([{'item_id':item.pk, 'quantity':'3'}]))
        self.assertEqual(self.balance(), Decimal('-30'))
        self.assertFalse(WarehouseMovement.objects.exists())
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk})
        self.assertEqual(response.context['lines'].paginator.count, 1)
        self.assertContains(response, 'Obriši pogrešan unos')

    def test_delete_goods_undoes_stock_and_unshipped_replacement_is_cancelled(self):
        entry = self.goods()
        self.post(action='delete_line', line_id=entry.lines.get().pk)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        self.assertEqual(self.balance(), 0)
        from .warehouse_ledger import create_replacement
        line = self.damaged_line()
        order = create_replacement(partner_id=self.partner.pk, line_id=line.pk, user=self.user)
        self.post(action='delete_line', line_id=line.pk)
        order.refresh_from_db()
        self.assertEqual(order.lager_status, Order.LagerStatus.OTKAZANO)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 0)
        self.assertEqual(self.balance(), 0)

    def test_delete_rejects_another_partners_line(self):
        line = self.goods().lines.get()
        other = WarehousePartner.objects.create(naziv='Tuđi partner')
        with self.assertRaises(MagacinError):
            post_entry(partner_id=other.pk, user=self.user, data={'action':'delete_line', 'line_id':line.pk, 'token':str(uuid4())})
        self.assertEqual(self.balance(), Decimal('30'))

    def test_no_active_articles_shows_settled_without_changing_history(self):
        from .views_ledger import partners_with_balance
        self.post(kind='credit', amount='10')
        partner = partners_with_balance().get(pk=self.partner.pk)
        self.assertFalse(partner.has_active_articles)
        self.assertEqual(partner.balance, Decimal('-10'))
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner':self.partner.pk})
        self.assertContains(response, '<strong>IZMIRENO</strong>', html=True)
        self.assertContains(response, 'Nema aktivnih artikala')
        entry = self.goods(kind='credit')
        self.assertTrue(partners_with_balance().get(pk=self.partner.pk).has_active_articles)
        self.post(action='delete_line', line_id=entry.lines.get().pk)
        self.assertFalse(partners_with_balance().get(pk=self.partner.pk).has_active_articles)
        self.assertEqual(self.balance(), Decimal('-10'))

    def test_paid_balance_displays_payment_resolution(self):
        self.post(kind='credit', amount='10')
        self.client.force_login(self.user)
        self.post(kind='payment', amount='4')
        url = reverse('staff_magacin_duguje')
        response = self.client.get(url, {'partner': self.partner.pk})
        self.assertNotContains(response, 'Dug riješen uplatom')
        self.post(kind='payment', amount='6')
        response = self.client.get(url, {'partner': self.partner.pk})
        self.assertContains(response, 'IZMIRENO UPLATOM')
        self.assertContains(response, 'Dug riješen uplatom')
        self.post(kind='credit', amount='5')
        response = self.client.get(url, {'partner': self.partner.pk})
        self.assertNotContains(response, 'Dug riješen uplatom')

    def test_default_partners_only_with_history_search_includes_others(self):
        untouched = WarehousePartner.objects.create(naziv='Kupac bez promjena')
        self.post(kind='debit', amount='10')
        self.post(kind='receipt', amount='10')
        self.client.force_login(self.user)
        url = reverse('staff_magacin_duguje')
        response = self.client.get(url)
        self.assertEqual([p.pk for p in response.context['partners']], [self.partner.pk])
        response = self.client.get(url, {'q': 'Kupac bez promjena'})
        self.assertEqual([p.pk for p in response.context['partners']], [untouched.pk])

    def test_fully_returned_articles_hidden_but_partial_returns_stay(self):
        entry = self.goods()
        line = entry.lines.get()
        self.client.force_login(self.user)
        url = reverse('staff_magacin_duguje')
        self.post(action='return', line_id=line.pk, location_id=self.location.pk, quantity='1')
        response = self.client.get(url, {'partner': self.partner.pk})
        self.assertEqual(response.context['lines'][0].remaining, 2)
        self.post(action='return', line_id=line.pk, location_id=self.location.pk, quantity='2')
        response = self.client.get(url, {'partner': self.partner.pk})
        self.assertEqual(response.context['lines'].paginator.count, 0)
        self.assertEqual(response.context['displayed_total'], 0)
        self.assertEqual(response.context['entries'].paginator.count, 3)

    def excess_invoice_fixture(self):
        import json
        source, source_item = self.linked_order()
        entry = self.post(action='excess', order_id=source.pk, missing_json=json.dumps([{'item_id': source_item.pk, 'quantity': '2'}]))
        target = Order.objects.create(broj='EXCESS-TARGET', ime_prezime=source.ime_prezime,
            telefon=source.telefon, adresa=source.adresa, grad=source.grad, ukupno=0, izvor=Order.Izvor.MAGACIN)
        return entry.lines.get(), target

    def test_excess_invoice_never_enters_picking_or_deducts_stock(self):
        from .warehouse_ledger import add_excess_to_invoice
        from .views import _build_order_packing_lines
        from .magacin import validate_order_stock, reserve_for_order
        line, target = self.excess_invoice_fixture()
        ordinary = OrderItem.objects.create(narudzba=target, artikal=self.product, naziv='Redovno', cijena=10, kolicina=1)
        reserve_for_order(target, self.product, 1, user=self.user)
        movements = WarehouseMovement.objects.count()
        add_excess_to_invoice(partner_id=self.partner.pk, line_id=line.pk, order_id=target.pk, user=self.user)
        add_excess_to_invoice(partner_id=self.partner.pk, line_id=line.pk, order_id=target.pk, user=self.user)
        self.assertEqual(target.stavke.count(), 2)
        self.assertEqual(WarehouseMovement.objects.count(), movements)
        self.assertEqual(target.magacin_holds.get().kolicina, 1)
        target.refresh_from_db()
        self.assertEqual(target.medjuzbir, Decimal('30'))
        invoice = target.stavke.get(ledger_excess_line=line)
        self.assertEqual(invoice.kolicina_faktura, 2)
        lines, _ = _build_order_packing_lines(target)
        self.assertEqual([row['item_id'] for row in lines], [ordinary.pk])
        validate_order_stock(target, user=self.user)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 9)
        self.assertEqual(self.balance(), 0)
        validate_order_stock(target, user=self.user)
        self.assertEqual(Entry.objects.filter(source_line=line, kind=Entry.Kind.SETTLED).count(), 1)

    def test_excess_invoice_rejects_foreign_and_validated_orders(self):
        from .warehouse_ledger import add_excess_to_invoice
        line, target = self.excess_invoice_fixture()
        foreign = Order.objects.create(broj='FOREIGN', ime_prezime='Drugi', telefon='999', ukupno=0)
        with self.assertRaises(MagacinError):
            add_excess_to_invoice(partner_id=self.partner.pk, line_id=line.pk, order_id=foreign.pk)
        target.lager_status = Order.LagerStatus.VALIDIRANO
        target.save()
        with self.assertRaises(MagacinError):
            add_excess_to_invoice(partner_id=self.partner.pk, line_id=line.pk, order_id=target.pk)
        self.assertFalse(OrderItem.objects.filter(ledger_excess_line=line).exists())

    def test_payment_removes_pending_excess_invoice_charge(self):
        from .warehouse_ledger import add_excess_to_invoice
        line, target = self.excess_invoice_fixture()
        add_excess_to_invoice(partner_id=self.partner.pk, line_id=line.pk, order_id=target.pk)
        self.post(kind='receipt', amount='20')
        self.assertFalse(target.stavke.exists())
        self.assertEqual(self.balance(), 0)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)

    def test_deleting_excess_removes_pending_invoice_without_stock_change(self):
        from .warehouse_ledger import add_excess_to_invoice
        line, target = self.excess_invoice_fixture()
        add_excess_to_invoice(partner_id=self.partner.pk, line_id=line.pk, order_id=target.pk)
        self.post(action='delete_line', line_id=line.pk)
        self.assertFalse(target.stavke.exists())
        self.assertEqual(self.balance(), 0)
        self.assertEqual(WarehouseMovement.objects.count(), 0)

    def test_excess_action_and_invoice_print_quantity(self):
        from .views import _order_print_job
        line, target = self.excess_invoice_fixture()
        self.client.force_login(self.user)
        url = reverse('staff_magacin_duguje')
        response = self.client.get(url, {'partner': self.partner.pk})
        self.assertNotContains(response, 'Dodaj u narudžbu bez pickinga')
        from .warehouse_ledger import attach_customer_excess
        attach_customer_excess(target, self.partner.customer, user=self.user)
        target.refresh_from_db()
        job = _order_print_job(target)
        self.assertEqual(len(job['stavke']), 1)
        self.assertEqual(job['stavke'][0]['kolicina'], 2)
        self.assertEqual(job['packing_lines'], [])
        from .magacin import validate_order_stock
        validate_order_stock(target, user=self.user)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        self.assertFalse(WarehouseMovement.objects.exists())

    def test_edit_manual_order_preserves_invoice_only_item(self):
        from .warehouse_ledger import add_excess_to_invoice
        line, target = self.excess_invoice_fixture()
        add_excess_to_invoice(partner_id=self.partner.pk, line_id=line.pk, order_id=target.pk)
        invoice_id = target.stavke.get().pk
        from django.utils import timezone
        self.product.magacin_sync_at = timezone.now()
        self.product.save()
        self.client.force_login(self.user)
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'order_broj': target.broj, 'ime_prezime': target.ime_prezime,
            'telefon': target.telefon, 'adresa': target.adresa, 'grad': target.grad,
            'product_id': [str(self.product.pk)], 'variation_id': [''], 'kolicina': ['1'],
            'mp_ok': ['0'], 'rezervni': ['0'], 'action': 'sacuvaj',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(target.stavke.filter(pk=invoice_id, ledger_excess_line=line).exists())
        target.refresh_from_db()
        self.assertEqual(target.medjuzbir, Decimal('30'))
        self.assertEqual(target.magacin_holds.get().kolicina, 1)

    def test_new_customer_order_automatically_adds_excess_once(self):
        from django.utils import timezone
        from .views import _build_order_packing_lines
        line, unused_target = self.excess_invoice_fixture()
        self.product.magacin_sync_at = timezone.now()
        self.product.save()
        self.client.force_login(self.user)
        lookup = self.client.get(reverse('staff_magacin_kupci_lookup'), {'q': self.partner.customer.ime_prezime})
        data = next(row for row in lookup.json()['results'] if row['id'] == self.partner.customer_id)
        self.assertEqual(data['excess_items'][0]['quantity'], 2)
        self.assertEqual(data['excess_items'][0]['name'], line.name)
        customer = self.partner.customer
        payload = {'ime_prezime': customer.ime_prezime, 'telefon': customer.telefon,
            'adresa': 'Test 1', 'grad': 'Sarajevo', 'postanski_broj': '71000',
            'product_id': [str(self.product.pk)], 'variation_id': [''], 'kolicina': ['1'],
            'mp_ok': ['0'], 'rezervni': ['0'], 'action': 'sacuvaj'}
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), payload)
        self.assertEqual(response.status_code, 302)
        invoice = OrderItem.objects.get(ledger_excess_line=line)
        order = invoice.narudzba
        self.assertEqual(invoice.kolicina_faktura, 2)
        self.assertEqual(order.medjuzbir, Decimal('30'))
        self.assertEqual(order.magacin_holds.get().kolicina, 1)
        rows, _ = _build_order_packing_lines(order)
        self.assertNotIn(invoice.pk, [row['item_id'] for row in rows])
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OrderItem.objects.filter(ledger_excess_line=line).count(), 1)
        self.assertEqual(OrderItem.objects.get(ledger_excess_line=line).narudzba_id, order.pk)
        lookup = self.client.get(reverse('staff_magacin_kupci_lookup'), {'q': customer.ime_prezime})
        self.assertEqual(next(row for row in lookup.json()['results'] if row['id'] == customer.pk)['excess_items'], [])

    def test_new_order_can_invoice_only_previous_excess(self):
        from .views import _build_order_packing_lines
        line, _ = self.excess_invoice_fixture()
        customer = self.partner.customer
        self.client.force_login(self.user)
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': customer.ime_prezime, 'telefon': customer.telefon,
            'adresa': 'Test 1', 'grad': 'Sarajevo', 'postanski_broj': '71000', 'action': 'sacuvaj',
        })
        self.assertEqual(response.status_code, 302)
        invoice = OrderItem.objects.get(ledger_excess_line=line)
        self.assertEqual(invoice.kolicina, 2)
        self.assertEqual(_build_order_packing_lines(invoice.narudzba)[0], [])
        self.assertFalse(invoice.narudzba.magacin_holds.exists())

    def missing_fulfillment_fixture(self):
        import json
        source, original = self.linked_order()
        entry = self.post(action='missing', order_id=source.pk, missing_json=json.dumps([{'item_id': original.pk, 'quantity': '2'}]))
        target = Order.objects.create(broj='MISSING-TARGET', ime_prezime=source.ime_prezime,
            telefon=source.telefon, adresa=source.adresa, grad=source.grad, ukupno=0, izvor=Order.Izvor.MAGACIN)
        return entry.lines.get(), target

    def test_missing_is_picked_not_printed_and_settles_once(self):
        from .warehouse_ledger import attach_customer_missing
        from .magacin import validate_order_stock, reserve_for_order, recalculate_order_totals
        from .views import _order_print_job
        line, target = self.missing_fulfillment_fixture()
        ordinary = OrderItem.objects.create(narudzba=target, artikal=self.product, naziv='Redovno', cijena=10, kolicina=1)
        reserve_for_order(target, self.product, 1, user=self.user)
        recalculate_order_totals(target)
        attach_customer_missing(target, self.partner.customer, user=self.user)
        self.assertEqual(attach_customer_missing(target, self.partner.customer, user=self.user), [])
        missing = target.stavke.get(ledger_missing_line=line)
        job = _order_print_job(target)
        self.assertEqual([row['kolicina'] for row in job['stavke']], [1])
        self.assertEqual({row['item_id'] for row in job['packing_lines']}, {ordinary.pk, missing.pk})
        self.assertEqual(target.medjuzbir, Decimal('10'))
        self.assertEqual(self.balance(), Decimal('-20'))
        missing.kolicina_pokupljeno = 2
        missing.save()
        validate_order_stock(target, user=self.user)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 7)
        self.assertEqual(self.balance(), 0)
        validate_order_stock(target, user=self.user)
        self.assertEqual(Entry.objects.filter(source_line=line, kind=Entry.Kind.SETTLED).count(), 1)
        self.client.force_login(self.user)
        response = self.client.get(reverse('staff_magacin_duguje'), {'partner': self.partner.pk})
        self.assertEqual(response.context['lines'].paginator.count, 0)

    def test_partial_missing_keeps_unpicked_debt_and_can_send_remainder(self):
        from .warehouse_ledger import attach_customer_missing, pending_missing_lines
        from .magacin import validate_order_stock
        line, target = self.missing_fulfillment_fixture()
        item = attach_customer_missing(target, self.partner.customer, user=self.user)[0]
        item.kolicina_pokupljeno = 1
        item.save()
        validate_order_stock(target, user=self.user)
        self.assertEqual(self.balance(), Decimal('-10'))
        pending = pending_missing_lines().get(pk=line.pk)
        self.assertEqual(pending.quantity - pending.returned, 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 9)

    def test_missing_without_stock_stays_owed_and_payment_removes_pending_pick(self):
        from .warehouse_ledger import attach_customer_missing
        line, target = self.missing_fulfillment_fixture()
        self.stock.kolicina = 0
        self.stock.save()
        self.assertEqual(attach_customer_missing(target, self.partner.customer, user=self.user), [])
        self.assertEqual(self.balance(), Decimal('-20'))
        self.stock.kolicina = 10
        self.stock.save()
        attach_customer_missing(target, self.partner.customer, user=self.user)
        self.post(kind='payment', amount='20')
        self.assertFalse(target.stavke.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 10)
        self.assertEqual(self.stock.rezervisano, 0)
        self.assertEqual(self.balance(), 0)

    def test_new_order_auto_adds_missing_and_picking_completion_closes_debt(self):
        from .views_magacin import _order_pick_bundle, apply_order_pick
        from .magacin import validate_order_stock
        from .views import _order_print_job
        line, _ = self.missing_fulfillment_fixture()
        customer = self.partner.customer
        self.client.force_login(self.user)
        lookup = self.client.get(reverse('staff_magacin_kupci_lookup'), {'q': customer.ime_prezime})
        found = next(row for row in lookup.json()['results'] if row['id'] == customer.pk)
        self.assertEqual(found['missing_items'][0]['quantity'], 2)
        response = self.client.post(reverse('staff_magacin_narudzba_nova'), {
            'ime_prezime': customer.ime_prezime, 'telefon': customer.telefon, 'adresa': 'Test 1',
            'grad': 'Sarajevo', 'postanski_broj': '71000', 'action': 'sacuvaj',
        })
        self.assertEqual(response.status_code, 302)
        item = OrderItem.objects.get(ledger_missing_line=line)
        order = item.narudzba
        queue, _, _ = _order_pick_bundle(order)
        self.assertEqual(sum(row['need'] for row in queue), 2)
        apply_order_pick(order, [dict(row, got=row['need'], done=True) for row in queue], finalize=True, user=self.user)
        validate_order_stock(order, user=self.user)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(_order_print_job(order)['stavke'], [])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 8)
