from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, RequestFactory

from . import live_visitor_offer as offers
from .models import LiveVisitorOffer


class RegistrationPopupRemovalTests(SimpleTestCase):
    def test_automatic_invites_are_disabled(self):
        with patch.object(offers, 'send_live_visitor_registration_invite') as send:
            self.assertIsNone(offers.maybe_auto_welcome_registration(RequestFactory().get('/')))
        send.assert_not_called()

    def test_existing_registration_invite_is_not_rendered_or_polled(self):
        request = RequestFactory().get('/')
        request.session = {}
        offer = SimpleNamespace(tip=LiveVisitorOffer.Tip.REGISTRACIJA)
        self.assertIsNone(offers._build_offer_payload(offer))
        with patch.object(offers, 'get_active_live_visitor_offer', return_value=offer), \
             patch.object(offers, 'mark_registration_invite_pending'), \
             patch.object(offers, 'maybe_create_product_dwell_offer'), \
             patch('EcommerceApp.browse_interest_offer.maybe_create_browse_interest_offer'):
            self.assertIsNone(offers.build_live_visitor_offer_context(request))
            self.assertIsNone(offers.poll_live_visitor_offer(request))

    def test_other_offer_types_keep_their_payloads(self):
        offer = SimpleNamespace(tip=LiveVisitorOffer.Tip.NARUDZBA)
        with patch.object(offers, '_build_order_offer_payload', return_value={'offer_type': 'order'}):
            self.assertEqual(offers._build_offer_payload(offer), {'offer_type': 'order'})
