from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (CardPayment, OnlineGiftClaim, OnlineGiftCampaign, Order, OrderItem,
                     OrderStockHold, Product, ScratchPrize, WarehouseLocation, WarehouseStock)
from .online_gift import SCRATCH_CAMPAIGN_NAME, _set_scratch_session_reward


@override_settings(SITE_PREP_ENABLED=False)
class ScratchOrderAddTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Osvojeni artikal', sifra='PRIZE', cijena=20,
                                               stanje=3, na_stanju=True)
        self.order = Order.objects.create(ime_prezime='Kupac', medjuzbir=50, ukupno=61,
                                          lager_status=Order.LagerStatus.REZERVISANO)
        self.campaign = OnlineGiftCampaign.objects.create(naziv=SCRATCH_CAMPAIGN_NAME)
        ScratchPrize.objects.create(campaign=self.campaign, code='offer', label='Artikal -50%',
            kind=ScratchPrize.Kind.PRODUCT_DISCOUNT, product=self.product, discount_percent=50)
        session = self.client.session
        self.claim = OnlineGiftClaim.objects.create(campaign=self.campaign, session_key=session.session_key,
            won=True, scratch_trigger_order=self.order, scratch_prize_code='offer')
        _set_scratch_session_reward(SimpleNamespace(session=session), self.claim)
        session.save()

    def add(self):
        return self.client.post(reverse('scratch_add_product_to_order'))

    def test_catalog_stock_reward_is_added_reserved_and_visible_in_picking(self):
        response = self.add()
        self.assertEqual(response.status_code, 200, response.content)
        stock = WarehouseStock.objects.get(product=self.product)
        self.assertEqual((stock.kolicina, stock.rezervisano), (3, 1))
        self.assertEqual(stock.location.sifra, 'WEB')
        item = OrderItem.objects.get(narudzba=self.order, artikal=self.product)
        self.assertEqual(item.cijena, Decimal('10.00'))
        self.assertEqual(OrderStockHold.objects.get(narudzba=self.order).kolicina, 1)
        self.order.refresh_from_db()
        self.assertEqual((self.order.medjuzbir, self.order.ukupno), (Decimal('60.00'), Decimal('71.00')))
        from .views_magacin import _order_pick_bundle
        queue, _, _ = _order_pick_bundle(self.order)
        self.assertTrue(any(row['item_id'] == item.pk for row in queue))
        self.claim.refresh_from_db()
        self.assertTrue(self.claim.reward_consumed)
        self.add()
        self.assertEqual(OrderItem.objects.filter(narudzba=self.order, artikal=self.product).count(), 1)
        stock.refresh_from_db()
        self.assertEqual(stock.rezervisano, 1)

    def test_reward_discount_uses_regular_price_when_product_is_on_sale(self):
        self.product.akcijska_cijena = Decimal('12.00')
        self.product.save()
        response = self.add()
        self.assertEqual(response.status_code, 200, response.content)
        item = OrderItem.objects.get(narudzba=self.order, artikal=self.product)
        self.assertEqual(item.bazna_cijena, Decimal('20.00'))
        self.assertEqual(item.cijena, Decimal('10.00'))
        self.assertEqual(item.popust_iznos, Decimal('10.00'))

    def test_cart_reward_discount_uses_regular_price_when_product_is_on_sale(self):
        self.product.akcijska_cijena = Decimal('12.00')
        self.product.save()
        response = self.client.post(reverse('scratch_add_product'))
        self.assertEqual(response.status_code, 200, response.content)
        from .cart import Cart
        cart = Cart(SimpleNamespace(session=self.client.session))
        self.assertEqual(cart.ukupno, Decimal('10.00'))

    def test_add_with_refreshed_csrf_token(self):
        self.client.handler.enforce_csrf_checks = True
        token = self.client.get(reverse('auth_csrf_token')).json()['csrfToken']
        response = self.client.post(reverse('scratch_add_product_to_order'),
                                    HTTP_X_CSRFTOKEN=token,
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.order.stavke.get().cijena, Decimal('10.00'))

    def test_reward_set_is_created_before_component_reservation(self):
        from .product_sets import save_set
        prize = ScratchPrize.objects.get(campaign=self.campaign, code='offer')
        bundle = save_set(name='Nagradni set', regular_price='40', rows=[
            {'product_id': self.product.pk, 'quantity': 2},
        ])
        prize.product = bundle
        prize.save()
        session = self.client.session
        _set_scratch_session_reward(SimpleNamespace(session=session), self.claim)
        session.save()
        response = self.add()
        self.assertEqual(response.status_code, 200, response.content)
        parent = self.order.stavke.get(artikal=bundle)
        self.assertTrue(parent.is_set_parent)
        self.assertEqual(parent.set_children.get().kolicina, 2)
        self.assertEqual(WarehouseStock.objects.get(product=self.product).rezervisano, 2)

    def test_long_product_name_fits_order_item_field(self):
        self.product.naziv = 'A' * 200
        self.product.save()
        response = self.add()
        self.assertEqual(response.status_code, 200, response.content)
        item = self.order.stavke.get()
        self.assertEqual(item.naziv, self.product.naziv)
        self.assertLessEqual(len(item.naziv), OrderItem._meta.get_field('naziv').max_length)

    def test_existing_physical_stock_is_not_rebuilt_from_catalog(self):
        location = WarehouseLocation.objects.create(sifra='A01', naziv='A01')
        stock = WarehouseStock.objects.create(product=self.product, location=location, kolicina=7)
        response = self.add()
        self.assertEqual(response.status_code, 200)
        stock.refresh_from_db()
        self.assertEqual((stock.kolicina, stock.rezervisano), (7, 1))
        self.assertEqual(WarehouseStock.objects.count(), 1)
        self.assertFalse(WarehouseLocation.objects.filter(sifra='WEB').exists())

    def test_unavailable_reward_keeps_order_claim_and_stock_unchanged(self):
        location = WarehouseLocation.objects.create(sifra='A01', naziv='A01')
        stock = WarehouseStock.objects.create(product=self.product, location=location, kolicina=0)
        response = self.add()
        self.assertEqual(response.status_code, 400)
        self.assertIn('lageru', response.json()['detail'])
        self.order.refresh_from_db(); self.claim.refresh_from_db(); stock.refresh_from_db()
        self.assertEqual(self.order.ukupno, Decimal('61.00'))
        self.assertFalse(self.claim.reward_consumed)
        self.assertFalse(self.order.stavke.exists())
        self.assertEqual((stock.kolicina, stock.rezervisano), (0, 0))

    def test_paid_card_amount_cannot_be_increased(self):
        CardPayment.objects.create(order=self.order, status='paid', amount=6100)
        response = self.add()
        self.assertEqual(response.status_code, 400)
        self.assertIn('kartično', response.json()['detail'])
        self.assertFalse(self.order.stavke.exists())
        self.assertFalse(WarehouseStock.objects.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.ukupno, Decimal('61.00'))

    def test_confirmed_open_order_accepts_reward(self):
        self.order.status = Order.Status.POTVRDJENA
        self.order.save(update_fields=['status'])
        response = self.add()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(self.order.stavke.filter(artikal=self.product).exists())

    def test_closed_or_validated_orders_reject_reward(self):
        for changes in ({'status': Order.Status.OTKAZANA},
                        {'status': Order.Status.POSLANA},
                        {'status': Order.Status.ZAVRSENA},
                        {'zapakovana': True}, {'stanje_skinuto': True},
                        {'lager_status': Order.LagerStatus.VALIDIRANO},
                        {'lager_status': Order.LagerStatus.OTKAZANO}):
            with self.subTest(changes=changes):
                Order.objects.filter(pk=self.order.pk).update(
                    status=Order.Status.NOVA, zapakovana=False, stanje_skinuto=False,
                    lager_status=Order.LagerStatus.REZERVISANO)
                Order.objects.filter(pk=self.order.pk).update(**changes)
                response = self.add()
                self.assertEqual(response.status_code, 400)
                self.assertFalse(self.order.stavke.exists())

    def test_pending_chances_choose_latest_open_order(self):
        from .online_gift import _next_pending_order, SCRATCH_PENDING_ORDERS_KEY
        older = Order.objects.create(ime_prezime='Starija', ukupno=20)
        newest = Order.objects.create(ime_prezime='Nova', ukupno=20)
        cancelled = Order.objects.create(ime_prezime='Otkazana', ukupno=20,
                                          status=Order.Status.OTKAZANA)
        request = SimpleNamespace(session={SCRATCH_PENDING_ORDERS_KEY:
            [older.pk, newest.pk, cancelled.pk]})
        self.assertEqual(_next_pending_order(request, self.campaign), newest.pk)
