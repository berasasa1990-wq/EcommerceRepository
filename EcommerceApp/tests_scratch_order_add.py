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
