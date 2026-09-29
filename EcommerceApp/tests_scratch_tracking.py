from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from .models import OnlineGiftClaim, Order
from .online_gift import SCRATCH_PENDING_ORDERS_KEY


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ScratchTrackingTests(TestCase):
    def setUp(self):
        self.order = Order.objects.create(broj='TRACK-1', ime_prezime='Kupac Test', email='kupac@example.com', telefon='061000000', adresa='Adresa 1', grad='Sarajevo', ukupno='10.00')
        session = self.client.session
        session[SCRATCH_PENDING_ORDERS_KEY] = [self.order.pk]
        session.save()
        with patch('EcommerceApp.online_gift.randbelow', return_value=0):
            response = self.client.post(reverse('scratch_claim'))
        self.claim = OnlineGiftClaim.objects.get(pk=response.json()['claim_id'])

    def event(self, event, client=None):
        return (client or self.client).post(reverse('scratch_event'), {'claim_id': self.claim.pk, 'event': event})

    def test_claim_is_not_automatically_a_view_or_scratch(self):
        self.assertTrue(self.claim.scratch_tracking_enabled)
        self.assertIsNone(self.claim.scratch_shown_at)
        self.assertIsNone(self.claim.scratch_revealed_at)
        self.assertEqual(self.claim.scratch_reward_label, '5% popusta')

    def test_events_are_idempotent_and_support_out_of_order_delivery(self):
        self.assertEqual(self.event('revealed').status_code, 200)
        self.claim.refresh_from_db()
        shown, revealed = self.claim.scratch_shown_at, self.claim.scratch_revealed_at
        self.assertIsNotNone(shown)
        self.assertIsNotNone(revealed)
        self.event('shown')
        self.event('revealed')
        self.claim.refresh_from_db()
        self.assertEqual(shown, self.claim.scratch_shown_at)
        self.assertEqual(revealed, self.claim.scratch_revealed_at)
        self.assertFalse(self.claim.reward_consumed)

    def test_other_session_cannot_record_events(self):
        self.assertEqual(self.event('shown', Client()).status_code, 404)
        self.assertEqual(self.event('invalid').status_code, 400)

    def test_report_permissions_and_customer_reward_order(self):
        url = reverse('staff_scratch_analytics')
        self.assertEqual(self.client.get(url).status_code, 302)
        User = get_user_model()
        regular = User.objects.create_user(username='ordinary')
        self.client.force_login(regular)
        self.assertEqual(self.client.get(url).status_code, 302)
        admin = User.objects.create_superuser(username='scratch-admin', email='admin@example.com', password='test')
        self.client.force_login(admin)
        self.claim.scratch_revealed_at = self.claim.kreirano
        self.claim.reward_consumed = True
        self.claim.order = self.order
        self.claim.save()
        with patch('EcommerceApp.views._base_context', return_value={}):
            response = self.client.get(url)
        self.assertContains(response, 'Kupac Test')
        self.assertContains(response, '5% popusta')
        self.assertContains(response, '#TRACK-1')
        self.assertEqual(response.context['accepted_count'], 1)
        self.assertContains(response, 'Prihvaćeno')
