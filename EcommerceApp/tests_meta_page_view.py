from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase

from .context_processors import meta_pixel
from .middleware.meta_page_view import MetaPageViewMiddleware


class MetaPageViewMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _run(self, request, response):
        order = []

        def get_response(_request):
            order.append(('response', _request.meta_page_view_event_id))
            return response

        with patch('EcommerceApp.middleware.meta_page_view.track_page_view') as track:
            track.side_effect = lambda *_args, **_kwargs: order.append('capi')
            result = MetaPageViewMiddleware(get_response)(request)
        return result, track, order

    def test_successful_html_get_sends_once_after_response_with_template_event_id(self):
        request = self.factory.get('/artikal/test/')
        response, track, order = self._run(request, HttpResponse('page', content_type='text/html; charset=utf-8'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(order, [('response', request.meta_page_view_event_id), 'capi'])
        track.assert_called_once_with(request, event_id=request.meta_page_view_event_id)
        self.assertRegex(request.meta_page_view_event_id, r'^pageview-[0-9a-f]{32}$')

        rendered_id = Template('{{ meta_page_view_event_id }}').render(Context(meta_pixel(request)))
        self.assertEqual(rendered_id, track.call_args.kwargs['event_id'])
        base_template = (Path(settings.BASE_DIR) / 'EcommerceApp/template/base.html').read_text()
        self.assertIn("{ eventID: metaPageViewEventId }", base_template)
        self.assertIn("'{{ meta_page_view_event_id|default:\"\"|escapejs }}'", base_template)

    def test_json_get_is_not_sent(self):
        request = self.factory.get('/drustveni-dokaz/')
        _, track, _ = self._run(request, JsonResponse({'ok': True}))
        track.assert_not_called()

    def test_other_successful_non_html_get_is_not_sent(self):
        request = self.factory.get('/download/')
        _, track, _ = self._run(request, HttpResponse('data', content_type='text/plain'))
        track.assert_not_called()

    def test_cart_stock_json_poll_is_not_sent(self):
        request = self.factory.get('/korpa/stanje/')
        _, track, _ = self._run(request, JsonResponse({'items': []}))
        track.assert_not_called()

    def test_redirect_and_error_responses_are_not_sent(self):
        for response in (
            HttpResponseRedirect('/'),
            HttpResponse(status=404, content_type='text/html'),
            HttpResponse(status=500, content_type='text/html'),
        ):
            with self.subTest(status=response.status_code):
                _, track, _ = self._run(self.factory.get('/artikal/test/'), response)
                track.assert_not_called()

    def test_head_and_existing_exclusions_stay_excluded(self):
        requests = [
            self.factory.head('/artikal/test/'),
            self.factory.get('/admin/'),
            self.factory.get('/api/products/'),
            self.factory.get('/static/site.css'),
            self.factory.get('/media/image.jpg'),
            self.factory.get('/nalog/'),
            self.factory.get('/sitemap.xml'),
            self.factory.get('/robots.txt'),
            self.factory.get('/favicon.ico'),
            self.factory.get('/healthz/'),
            self.factory.get('/facebook-feed.xml'),
            self.factory.get('/uzivo/prisutan/'),
            self.factory.get('/artikal/test/', HTTP_X_REQUESTED_WITH='XMLHttpRequest'),
        ]
        for request in requests:
            with self.subTest(method=request.method, path=request.path):
                _, track, _ = self._run(request, HttpResponse('page', content_type='text/html'))
                self.assertIsNone(request.meta_page_view_event_id)
                track.assert_not_called()
