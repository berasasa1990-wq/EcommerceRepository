from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase

from .live_visitors import heartbeat_live_visitor, should_track_visitor
from .middleware.live_visitor import LiveVisitorMiddleware


class VisitorFilterTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory(HTTP_USER_AGENT='Mozilla/5.0 Chrome/130.0 Safari/537.36')
        self.ip_patch = patch('EcommerceApp.live_visitors.is_excluded_visitor_ip', return_value=False)
        self.ip_patch.start()
        self.addCleanup(self.ip_patch.stop)

    def request(self, path='/', **kwargs):
        request = self.factory.get(path, **kwargs)
        request.session = SimpleNamespace(session_key='visitor-session')
        return request

    def test_browsers_allowed_and_known_bots_excluded(self):
        self.assertTrue(should_track_visitor(self.request()))
        for ua in ('', 'Googlebot', 'python-httpx/0.28', 'Mozilla/5.0 HeadlessChrome/130', 'meta-externalagent'):
            with self.subTest(ua=ua):
                self.assertFalse(should_track_visitor(self.request(HTTP_USER_AGENT=ua)))

    def test_cms_and_asset_requests_excluded_even_with_browser_ua(self):
        for path in ('/wp-content/uploads/2022/11/BKK-Hooked-on-fishing-black-sajt.jpg', '/wp-login.php', '/.env', '/image.JPG'):
            with self.subTest(path=path):
                self.assertFalse(should_track_visitor(self.request(path)))

    def test_only_successful_html_get_is_tracked(self):
        for response, expected in ((HttpResponse('page'), True), (HttpResponse(status=404), False),
                                   (HttpResponse(status=500), False), (HttpResponse(status=302), False),
                                   (JsonResponse({'ok': True}), False),
                                   (HttpResponse('image', content_type='image/jpeg'), False)):
            with self.subTest(status=response.status_code, content_type=response['Content-Type']):
                with patch('EcommerceApp.middleware.live_visitor.track_live_visitor') as track:
                    LiveVisitorMiddleware(lambda request: response)(self.request())
                    self.assertEqual(track.call_count, int(expected))

    def test_head_is_not_page_visit(self):
        request = self.request()
        request.method = 'HEAD'
        with patch('EcommerceApp.middleware.live_visitor.track_live_visitor') as track:
            LiveVisitorMiddleware(lambda request: HttpResponse())(request)
            track.assert_not_called()

    def test_heartbeat_cannot_bypass_bot_or_asset_filter(self):
        self.assertFalse(heartbeat_live_visitor(self.request('/uzivo/prisutan/', HTTP_USER_AGENT='Googlebot')))
        self.assertFalse(heartbeat_live_visitor(self.request('/uzivo/prisutan/?path=/wp-content/image.jpg')))
