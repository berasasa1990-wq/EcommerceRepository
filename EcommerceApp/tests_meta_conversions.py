from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, override_settings

from . import meta_conversions


@override_settings(META_PIXEL_ID='1059983681812075', META_ACCESS_TOKEN='test-token', META_TEST_EVENT_CODE='')
class MetaCapiUserDataTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.product = SimpleNamespace(pk=7, sifra='SKU-7', naziv='Test product', prikazna_cijena=Decimal('12'))

    def _request(self, *, cookies=False):
        headers = {
            'REMOTE_ADDR': '10.0.0.1',
            'HTTP_X_FORWARDED_FOR': '198.51.100.24, 10.0.0.1',
            'HTTP_USER_AGENT': 'Test browser/1.0',
        }
        if cookies:
            headers['HTTP_COOKIE'] = '_fbp=fb.1.123.456; _fbc=fb.1.123.click'
        request = self.factory.get('/product/', **headers)
        request.user = AnonymousUser()
        request.session = {}
        return request

    def _post_event(self, callback):
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {'events_received': 1}
        with patch.object(meta_conversions.threading, 'Thread') as thread, \
             patch.object(meta_conversions.requests, 'post', return_value=response) as post, \
             patch.object(meta_conversions.logger, 'warning'):
            callback()
            thread.assert_called_once()
            target = thread.call_args.kwargs['target']
            target(*thread.call_args.kwargs['args'])
            return post.call_args.kwargs['json']['data'][0]

    def test_page_view_sends_ip_and_user_agent_in_final_json(self):
        event = self._post_event(lambda: meta_conversions.track_page_view(self._request(), event_id='pageview-fixed'))
        self.assertEqual(event['event_name'], 'PageView')
        self.assertEqual(event['event_id'], 'pageview-fixed')
        self.assertEqual(event['user_data'], {
            'client_ip_address': '198.51.100.24',
            'client_user_agent': 'Test browser/1.0',
        })

    def test_page_view_includes_available_fbp_and_fbc_unchanged(self):
        event = self._post_event(lambda: meta_conversions.track_page_view(self._request(cookies=True), event_id='pageview-cookies'))
        self.assertEqual(event['user_data']['fbp'], 'fb.1.123.456')
        self.assertEqual(event['user_data']['fbc'], 'fb.1.123.click')

    def test_view_content_uses_same_user_data_and_keeps_event_id(self):
        event = self._post_event(lambda: meta_conversions.track_view_content(
            self._request(cookies=True), self.product, event_id='viewcontent-fixed',
        ))
        self.assertEqual(event['event_name'], 'ViewContent')
        self.assertEqual(event['event_id'], 'viewcontent-fixed')
        self.assertEqual(event['user_data'], {
            'client_ip_address': '198.51.100.24',
            'client_user_agent': 'Test browser/1.0',
            'fbp': 'fb.1.123.456',
            'fbc': 'fb.1.123.click',
        })

    def test_add_to_cart_uses_same_user_data_and_keeps_event_id(self):
        event = self._post_event(lambda: meta_conversions.track_add_to_cart(
            self._request(cookies=True), self.product, event_id='addtocart-fixed',
        ))
        self.assertEqual(event['event_name'], 'AddToCart')
        self.assertEqual(event['event_id'], 'addtocart-fixed')
        self.assertEqual(event['user_data'], {
            'client_ip_address': '198.51.100.24',
            'client_user_agent': 'Test browser/1.0',
            'fbp': 'fb.1.123.456',
            'fbc': 'fb.1.123.click',
        })

    def test_available_email_is_hashed_without_replacing_request_matching_fields(self):
        request = self._request(cookies=True)
        request.user = SimpleNamespace(is_authenticated=True, email=' Person@Example.COM ')
        event = self._post_event(lambda: meta_conversions.track_view_content(request, self.product))
        self.assertEqual(event['user_data']['em'], [meta_conversions.hash_email(request.user.email)])
        self.assertEqual(event['user_data']['client_ip_address'], '198.51.100.24')
        self.assertEqual(event['user_data']['fbp'], 'fb.1.123.456')

        request.user = AnonymousUser()
        request.session['checkout_email'] = ' Guest@Example.COM '
        event = self._post_event(lambda: meta_conversions.track_add_to_cart(request, self.product))
        self.assertEqual(event['user_data']['em'], [meta_conversions.hash_email(request.session['checkout_email'])])
        self.assertEqual(event['user_data']['client_user_agent'], 'Test browser/1.0')
        self.assertEqual(event['user_data']['fbc'], 'fb.1.123.click')

    def test_purchase_keeps_its_existing_matching_fields_and_event_id(self):
        line = SimpleNamespace(sifra='SKU-7', artikal_id=7, pk=1, kolicina=1, cijena=Decimal('12'))
        order = SimpleNamespace(
            broj='0391', ime_prezime='Test Customer', email=' Test@Example.COM ',
            telefon='061234567', ukupno=Decimal('12'),
            stavke=SimpleNamespace(all=lambda: [line]),
        )
        event = self._post_event(lambda: meta_conversions.track_purchase(
            self._request(cookies=True), order, event_id='purchase-0391',
        ))
        self.assertEqual(event['event_name'], 'Purchase')
        self.assertEqual(event['event_id'], 'purchase-0391')
        self.assertEqual(event['user_data']['em'], [meta_conversions.hash_email(order.email)])
        self.assertEqual(event['user_data']['ph'], [meta_conversions.hash_phone(order.telefon)])
        self.assertEqual(event['user_data']['client_ip_address'], '198.51.100.24')
        self.assertEqual(event['user_data']['client_user_agent'], 'Test browser/1.0')
        self.assertEqual(event['user_data']['fbp'], 'fb.1.123.456')
        self.assertEqual(event['user_data']['fbc'], 'fb.1.123.click')
