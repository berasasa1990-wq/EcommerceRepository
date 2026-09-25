from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .models import OnlineGiftClaim, Order, ScratchPrize
from .online_gift import SCRATCH_PENDING_ORDERS_KEY, SCRATCH_SESSION_KEY, SESSION_REWARD_KEY


class ScratchGameTests(TestCase):
    def _order(self, number):
        return Order.objects.create(
            broj=number, ime_prezime='Test kupac', email='test@example.com',
            telefon='061000000', adresa='Test adresa 1', grad='Sarajevo', ukupno='10.00',
        )

    def _grant_order_chance(self, order):
        session = self.client.session
        session[SCRATCH_PENDING_ORDERS_KEY] = list(session.get(SCRATCH_PENDING_ORDERS_KEY, [])) + [order.pk]
        session.save()

    def test_every_completed_order_has_one_server_assigned_claim(self):
        """Dva zahtjeva ne smiju izvući dvije nagrade za istu narudžbu."""
        first_order = self._order('SCRATCH-1')
        self._grant_order_chance(first_order)
        self.assertTrue(self.client.get(reverse('scratch_status')).json()['eligible'])

        # randbelow(100) == 0 odgovara prvoj, 5% nagradi u ponderisanoj listi.
        with patch('EcommerceApp.online_gift.randbelow', return_value=0):
            first = self.client.post(reverse('scratch_claim')).json()
        repeated = self.client.post(reverse('scratch_claim')).json()

        self.assertTrue(first['ok'])
        self.assertTrue(first['created'])
        self.assertEqual(first['label'], '5% popusta')
        self.assertFalse(repeated['ok'])
        self.assertEqual(OnlineGiftClaim.objects.filter(scratch_trigger_order=first_order).count(), 1)
        self.assertFalse(self.client.get(reverse('scratch_status')).json()['eligible'])

        # Naredna prilika čeka dok se prethodna nagrada ne potroši.
        OnlineGiftClaim.objects.filter(scratch_trigger_order=first_order).update(reward_consumed=True)
        session = self.client.session
        session.pop(SCRATCH_SESSION_KEY, None)
        session.pop(SESSION_REWARD_KEY, None)
        session.save()
        second_order = self._order('SCRATCH-2')
        self._grant_order_chance(second_order)
        self.assertTrue(self.client.get(reverse('scratch_status')).json()['eligible'])
        with patch('EcommerceApp.online_gift.randbelow', return_value=0):
            self.client.post(reverse('scratch_claim'))
        self.assertEqual(OnlineGiftClaim.objects.filter(scratch_trigger_order=second_order).count(), 1)

    def test_no_popup_or_pending_chance_when_all_admin_prizes_are_inactive(self):
        ScratchPrize.objects.update(active=False)
        order = self._order('SCRATCH-OFF')

        # Simulira ranije sačuvanu priliku: ni ona se ne smije ponuditi kada
        # administrator ugasi sve nagrade.
        self._grant_order_chance(order)

        self.assertFalse(self.client.get(reverse('scratch_status')).json()['eligible'])
        response = self.client.post(reverse('scratch_claim'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(OnlineGiftClaim.objects.filter(scratch_trigger_order=order).count(), 0)
