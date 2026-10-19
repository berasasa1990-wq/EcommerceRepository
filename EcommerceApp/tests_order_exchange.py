from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Order, OrderItem, Product, WarehouseLocation, WarehouseStock
from .xexpress_service import build_shipment_payload, XExpressError


@override_settings(ALLOWED_HOSTS=['testserver'], STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class OrderExchangeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('exchange-admin', is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        self.product = Product.objects.create(naziv='Exchange product', cijena=30, stanje=10, na_stanju=True)
        self.location = WarehouseLocation.objects.create(sifra='EX-A', naziv='Exchange shelf')
        self.stock = WarehouseStock.objects.create(product=self.product, location=self.location, kolicina=10)
        self.order = Order.objects.create(ime_prezime='Kupac', email='kupac@example.com', telefon='065123456',
            adresa='Adresa 12', grad='Bijeljina', postanski_broj='76300',
            ukupno=Decimal('71'), medjuzbir=60, dostava=11, status=Order.Status.POSLANA,
            xexpress_sifra='OLD123', lager_status=Order.LagerStatus.VALIDIRANO)
        self.item = OrderItem.objects.create(narudzba=self.order, artikal=self.product, naziv=self.product.naziv,
                                 sifra='EX', cijena=30, kolicina=2)
        self.selection = {'item_id': str(self.item.pk), f'quantity_{self.item.pk}': '2'}
        self.url = reverse('staff_magacin_order_exchange', args=[self.order.broj])

    def test_creates_reserved_exchange_without_copying_dispatch_or_payment(self):
        self.assertEqual(self.client.post(self.url, self.selection).status_code, 302)
        exchange = self.order.exchange_orders.get()
        self.assertEqual(exchange.ime_prezime, self.order.ime_prezime)
        self.assertEqual(exchange.adresa, self.order.adresa)
        self.assertEqual(exchange.ukupno, 0)
        self.assertEqual(exchange.medjuzbir, 60)
        from .pricing import sazetak_iz_narudzbe
        from .magacin import recalculate_order_totals
        recalculate_order_totals(exchange)
        self.assertEqual(sazetak_iz_narudzbe(exchange)['ukupno'], 0)
        self.assertEqual(exchange.status, Order.Status.NOVA)
        self.assertEqual(exchange.xexpress_sifra, '')
        self.assertEqual(exchange.stavke.get().artikal_id, self.product.pk)
        self.assertEqual(exchange.stavke.get().kolicina, 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 2)
        self.client.post(self.url, self.selection)
        self.assertEqual(self.order.exchange_orders.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 2)
        self.order.refresh_from_db()
        self.assertEqual(self.order.ukupno, 71)
        self.assertEqual(self.order.xexpress_sifra, 'OLD123')

    def test_insufficient_stock_rolls_back_entire_exchange(self):
        WarehouseStock.objects.filter(pk=self.stock.pk).update(kolicina=1)
        self.client.post(self.url, self.selection)
        self.assertFalse(self.order.exchange_orders.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 0)

    def test_only_authorized_post_can_create_exchange(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.client.post(self.url, self.selection)
        self.assertFalse(self.order.exchange_orders.exists())

    def test_xexpress_requires_confirmed_exchange_field_and_never_cod(self):
        self.client.post(self.url, self.selection)
        exchange = self.order.exchange_orders.get()
        with override_settings(XEXPRESS_EXCHANGE_FIELD=''):
            with self.assertRaisesMessage(XExpressError, 'XEXPRESS_EXCHANGE_FIELD'):
                build_shipment_payload(exchange)
        # Configurable field name until the courier confirms its schema.
        with override_settings(XEXPRESS_EXCHANGE_FIELD='confirmedExchangeFlag'):
            payload = build_shipment_payload(exchange)
        self.assertTrue(payload['confirmedExchangeFlag'])
        self.assertFalse(payload['otkupnina'])
        self.assertEqual(payload['iznosOtkupnine'], 0)
        self.assertEqual(payload['vrednostPosiljke'], 60)

    @override_settings(XEXPRESS_EXCHANGE_FIELD='')
    def test_existing_regular_xexpress_shipping_remains_available(self):
        payload = build_shipment_payload(self.order)
        self.assertTrue(payload['otkupnina'])
        self.assertEqual(payload['iznosOtkupnine'], 71)

    def test_only_selected_item_and_quantity_are_copied(self):
        OrderItem.objects.create(narudzba=self.order, artikal=self.product, naziv='Not selected', cijena=15, kolicina=3)
        response = self.client.post(self.url, {'item_id': str(self.item.pk), f'quantity_{self.item.pk}': '1'})
        self.assertEqual(response.status_code, 302)
        exchange = self.order.exchange_orders.get()
        self.assertEqual(exchange.stavke.count(), 1)
        self.assertEqual(exchange.stavke.get().kolicina, 1)
        self.assertEqual(exchange.medjuzbir, 30)
        self.assertEqual(exchange.ime_prezime, self.order.ime_prezime)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 1)

    def test_invalid_selection_does_not_create_or_reserve(self):
        for payload in [{}, {'item_id': '999999', 'quantity_999999': '1'},
                        {'item_id': str(self.item.pk), f'quantity_{self.item.pk}': '3'},
                        {'item_id': str(self.item.pk), f'quantity_{self.item.pk}': '0'},
                        {'item_id': str(self.item.pk), f'quantity_{self.item.pk}': '1.5'}]:
            with self.subTest(payload=payload):
                self.client.post(self.url, payload)
                self.assertFalse(self.order.exchange_orders.exists())
                self.stock.refresh_from_db()
                self.assertEqual(self.stock.rezervisano, 0)

    def test_new_unvalidated_order_cannot_be_exchanged(self):
        from django.template.loader import render_to_string
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.NOVA, lager_status=Order.LagerStatus.REZERVISANO)
        self.order.refresh_from_db()
        html = render_to_string('staff/magacin/partials/order_actions_dialog.html', {'order': self.order})
        self.assertNotIn('data-order-exchange>', html)
        self.client.post(self.url, self.selection)
        self.assertFalse(self.order.exchange_orders.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.rezervisano, 0)
        self.order.lager_status = Order.LagerStatus.VALIDIRANO
        html = render_to_string('staff/magacin/partials/order_actions_dialog.html', {'order': self.order})
        self.assertIn('data-order-exchange>', html)
