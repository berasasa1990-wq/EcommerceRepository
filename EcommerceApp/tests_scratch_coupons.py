from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import CheckoutForm
from .models import Coupon, OnlineGiftCampaign, OnlineGiftClaim, Order, Product, ScratchPrize, WarehouseStock
from .online_gift import (SCRATCH_CAMPAIGN_NAME, SCRATCH_PENDING_ORDERS_KEY, _set_scratch_session_reward,
                         _scratch_claim_from_request, ensure_scratch_coupon, scratch_cart_reward)
from .online_gift import send_scratch_coupon_email
from .loyalty import validiraj_kupon
from .magacin import MagacinError, reserve_web_order_stock
from .pricing import izracunaj_sazetak


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_PREP_ENABLED=False)
class ScratchCouponTests(TestCase):
    def setUp(self):
        self.campaign = OnlineGiftCampaign.objects.create(naziv=SCRATCH_CAMPAIGN_NAME)
        self.prize = ScratchPrize.objects.create(campaign=self.campaign, code='percent', label='15% popusta',
            kind=ScratchPrize.Kind.PERCENT, discount_percent=15)
        self.order = Order.objects.create(ime_prezime='Kupac', email='guest@example.com', ukupno=100)
        self.claim = OnlineGiftClaim.objects.create(campaign=self.campaign, scratch_prize_code='percent',
            scratch_trigger_order=self.order, discount_percent=15, won=True, prize_type='percent')

    def test_old_reward_creates_persistent_code_and_emails_guest_once(self):
        OnlineGiftClaim.objects.filter(pk=self.claim.pk).update(kreirano=timezone.now() - timedelta(days=30))
        session = self.client.session
        request = SimpleNamespace(session=session)
        with patch('EcommerceApp.online_gift.Thread') as delivery:
            with self.captureOnCommitCallbacks(execute=True):
                _set_scratch_session_reward(request, self.claim)
            delivery.return_value.start.assert_called_once()
        send_scratch_coupon_email(self.claim.pk)
        self.assertIsNotNone(_scratch_claim_from_request(request))
        coupon = Coupon.objects.get(scratch_claim=self.claim)
        self.assertIsNone(coupon.vlasnik_id)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['guest@example.com'])
        self.assertIn(coupon.kod, mail.outbox[0].body)
        self.assertIn('nema rok isteka', mail.outbox[0].body)
        self.assertEqual(ensure_scratch_coupon(self.claim).pk, coupon.pk)
        self.claim.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            _set_scratch_session_reward(request, self.claim)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(scratch_cart_reward(request, 100)['active'])

    def test_coupon_is_ready_without_waiting_for_email_delivery(self):
        request = SimpleNamespace(session=self.client.session)
        with patch('EcommerceApp.online_gift.Thread') as delivery, \
                patch('EcommerceApp.online_gift.send_scratch_coupon_email') as send:
            with self.captureOnCommitCallbacks(execute=True):
                _set_scratch_session_reward(request, self.claim)
                delivery.assert_not_called()
                self.assertTrue(request.session['online_gift_reward']['coupon_code'])
            delivery.return_value.start.assert_called_once()
            send.assert_not_called()

    def test_background_email_closes_database_connections(self):
        from .online_gift import _send_scratch_coupon_in_background
        with patch('django.db.close_old_connections') as prepare, \
                patch('django.db.connections.close_all') as cleanup, \
                patch('EcommerceApp.online_gift.send_scratch_coupon_email') as send:
            _send_scratch_coupon_in_background(self.claim.pk)
            prepare.assert_called_once()
            send.assert_called_once_with(self.claim.pk)
            cleanup.assert_called_once()

    def test_registered_reward_belongs_to_account(self):
        user = User.objects.create_user('buyer', 'buyer@example.com')
        self.claim.user = user
        self.claim.save()
        coupon = ensure_scratch_coupon(self.claim)
        self.assertEqual(coupon.vlasnik, user)
        self.assertIsNone(validiraj_kupon(coupon.kod, AnonymousUser())[0])
        self.assertEqual(validiraj_kupon(coupon.kod, user)[0], coupon)

    def test_guest_can_enter_code_and_minimum_is_enforced(self):
        self.prize.minimum = 100
        self.prize.save()
        coupon = ensure_scratch_coupon(self.claim)
        self.assertIsNone(validiraj_kupon(coupon.kod, subtotal=99)[0])
        self.assertEqual(validiraj_kupon(coupon.kod, subtotal=100)[0], coupon)
        from .cart import Cart
        product = Product.objects.create(naziv='Artikal', cijena=100, stanje=3, na_stanju=True)
        session = self.client.session
        Cart(SimpleNamespace(session=session)).add(product)
        session.save()
        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'name="kod"')

    def test_coupon_uses_regular_price_without_stacking_sale(self):
        coupon = ensure_scratch_coupon(self.claim)
        items = [{'quantity': 1, 'ukupno_stavka': Decimal('90'), 'cijena_decimal': Decimal('90'),
                  'bazna_cijena_decimal': Decimal('100')}]
        summary = izracunaj_sazetak(90, coupon_code=coupon.kod, cart_items=items)
        self.assertEqual(summary['kupon_popust'], Decimal('5.00'))
        items[0]['ukupno_stavka'] = Decimal('70')
        summary = izracunaj_sazetak(70, coupon_code=coupon.kod, cart_items=items)
        self.assertEqual(summary['kupon_popust'], Decimal('0.00'))

    def test_successful_order_consumes_code_and_rejects_second_use(self):
        coupon = ensure_scratch_coupon(self.claim)
        order = Order.objects.create(ime_prezime='Sljedeća', email='guest@example.com',
                                     medjuzbir=100, ukupno=85, kupon_kod=coupon.kod)
        reserve_web_order_stock(order)
        self.claim.refresh_from_db()
        coupon.refresh_from_db()
        self.assertTrue(self.claim.reward_consumed)
        self.assertFalse(coupon.aktivan)
        self.assertEqual(self.claim.order, order)
        second = Order.objects.create(ime_prezime='Ponovo', medjuzbir=100, ukupno=85, kupon_kod=coupon.kod)
        with self.assertRaises(MagacinError):
            reserve_web_order_stock(second)

    def test_email_is_required_for_cod_checkout(self):
        data = dict(ime_prezime='Kupac', telefon='061123456', adresa='Ulica 1', grad='Sarajevo',
                    postanski_broj='71000', payment_method='cod')
        form = CheckoutForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)
        self.assertIn('required', str(form['email']))
        self.assertTrue(CheckoutForm({**data, 'email': 'guest@example.com'}).is_valid())
