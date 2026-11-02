import asyncio
import gzip
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import MiddlewareNotUsed
from django.core.management import call_command
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from .middleware.http_diagnostics import HttpDiagnosticsMiddleware
from .views_feed import facebook_feed


@override_settings(HTTP_DIAGNOSTICS_ENABLED=True, HTTP_DIAGNOSTICS_SAMPLE_RATE=1,
                   ALLOWED_HOSTS=['testserver'])
class DiagnosticsTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/secret-person/token?password=private',
            HTTP_USER_AGENT='Googlebot private@example.com', HTTP_COOKIE='secret=value')
        self.request.resolver_match = SimpleNamespace(route='order/<str:token>/')

    def run_response(self, response):
        return HttpDiagnosticsMiddleware(lambda request: response)(self.request)

    @override_settings(HTTP_DIAGNOSTICS_ENABLED=False)
    def test_disabled_is_removed(self):
        with self.assertRaises(MiddlewareNotUsed):
            HttpDiagnosticsMiddleware(lambda request: HttpResponse())

    def test_privacy_and_encoded_body_count(self):
        data = gzip.compress(b'large body ' * 1000)
        response = HttpResponse(data, headers={'Content-Encoding': 'gzip'})
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            self.assertIs(self.run_response(response), response)
        row = json.loads(logs.records[0].message)
        self.assertEqual(row['body_bytes'], len(data))
        self.assertEqual(row['route'], '/order/<str:token>/')
        self.assertEqual(row['ua_category'], 'google_claimed')
        self.assertEqual(row['host'], 'testserver')
        for private in ('private', 'secret-person', 'password', 'cookie', '@'):
            self.assertNotIn(private, logs.records[0].message)

    def test_unknown_and_static_paths_are_redacted(self):
        self.request.resolver_match = None
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            self.run_response(HttpResponse())
        self.assertEqual(json.loads(logs.records[0].message)['route'], '(unresolved)')
        self.request.path = '/static/user@example.com.png'
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            self.run_response(HttpResponse())
        self.assertEqual(json.loads(logs.records[0].message)['route'], '/static/*')

    def test_head_and_304_count_no_body(self):
        for method, status in [('HEAD', 200), ('GET', 304)]:
            self.request.method = method
            with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
                self.run_response(HttpResponse(b'prepared', status=status))
            self.assertEqual(json.loads(logs.records[0].message)['body_bytes'], 0)

    def test_stream_is_lazy_complete_and_closed_once(self):
        seen = []
        def chunks():
            seen.append(1)
            yield b'abc'
            yield b'de'
        response = self.run_response(StreamingHttpResponse(chunks()))
        self.assertEqual(seen, [])
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            self.assertEqual(b''.join(response), b'abcde')
            response.close()
        self.assertEqual(len(logs.records), 1)
        self.assertTrue(json.loads(logs.records[0].message)['complete'])
        self.assertEqual(json.loads(logs.records[0].message)['body_bytes'], 5)

    def test_partial_and_failed_stream(self):
        def chunks():
            yield b'abc'
            raise ValueError('sensitive error')
        for partial in (True, False):
            response = self.run_response(StreamingHttpResponse(chunks()))
            with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
                iterator = iter(response)
                self.assertEqual(next(iterator), b'abc')
                if not partial:
                    with self.assertRaises(ValueError):
                        next(iterator)
                response.close()
            row = json.loads(logs.records[0].message)
            self.assertFalse(row['complete'])
            self.assertEqual(row['body_bytes'], 3)
            self.assertEqual(len(logs.records), 1)
            self.assertNotIn('sensitive', logs.records[0].message)

    def test_unstarted_stream_is_not_consumed_on_close(self):
        seen = []
        def chunks():
            seen.append(1)
            yield b'abc'
        response = self.run_response(StreamingHttpResponse(chunks()))
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            response.close()
        self.assertEqual(seen, [])
        row = json.loads(logs.records[0].message)
        self.assertEqual(row['body_bytes'], 0)
        self.assertFalse(row['complete'])

    def test_async_stream(self):
        async def chunks():
            yield b'ab'
            yield b'c'
        response = self.run_response(StreamingHttpResponse(chunks()))
        async def consume():
            return b''.join([chunk async for chunk in response])
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            self.assertEqual(asyncio.run(consume()), b'abc')
            response.close()
        self.assertEqual(json.loads(logs.records[0].message)['body_bytes'], 3)

    def test_file_preserves_sendfile_and_marks_length_unverified(self):
        file = io.BytesIO(b'abcdef')
        response = FileResponse(file)
        original_iterator = response._iterator
        self.run_response(response)
        self.assertIs(response.file_to_stream, file)
        self.assertIs(response._iterator, original_iterator)
        self.assertEqual(file.tell(), 0)
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            response.close()
        row = json.loads(logs.records[0].message)
        self.assertEqual(row['body_bytes'], 6)
        self.assertIsNone(row['complete'])
        self.assertTrue(file.closed)

    @override_settings(HTTP_DIAGNOSTICS_SAMPLE_RATE=0)
    def test_zero_sampling_and_logging_failure_preserve_response(self):
        response = HttpResponse('ok')
        with patch('EcommerceApp.middleware.http_diagnostics.logger.info') as log:
            self.assertIs(self.run_response(response), response)
            log.assert_not_called()
        with override_settings(HTTP_DIAGNOSTICS_SAMPLE_RATE=1), patch(
            'EcommerceApp.middleware.http_diagnostics.logger.info', side_effect=RuntimeError):
            self.assertIs(self.run_response(response), response)


@override_settings(ALLOWED_HOSTS=['testserver'], CACHES={'default': {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'feed-bandwidth-tests'}})
class FeedCompressionTests(SimpleTestCase):
    @override_settings(TEMPLATES=[{
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [str(Path(__file__).resolve().parent / 'template')],
        'APP_DIRS': True,
        'OPTIONS': {'context_processors': ['django.template.context_processors.request']},
    }])
    def test_real_xml_preserves_catalog_data_media_domain_and_crawler_access(self):
        from xml.etree import ElementTree
        cache.clear()
        item = {'id': 'SKU-1', 'title': 'Štap & pribor',
            'description': 'Opis ' * 200, 'availability': 'in stock',
            'condition': 'new', 'price': '10.00 BAM',
            'link': 'https://carpologijabh.ba/artikal/test/',
            'image_link': 'https://media.opremazaribolov.ba/test.jpg'}
        factory = RequestFactory()
        with patch('EcommerceApp.views_feed._feed_items', return_value=[item]):
            for ua in ('Googlebot', 'bingbot', 'facebookexternalhit', 'Instagram'):
                response = facebook_feed(factory.get('/facebook-feed.xml',
                    HTTP_ACCEPT_ENCODING='gzip', HTTP_USER_AGENT=ua))
                self.assertEqual(response.status_code, 200)
                root = ElementTree.fromstring(gzip.decompress(response.content))
                self.assertEqual(root.find('.//{http://base.google.com/ns/1.0}title').text, item['title'])
                self.assertEqual(root.find('.//{http://base.google.com/ns/1.0}image_link').text, item['image_link'])

    def test_compression_variants_cache_and_conditional_response(self):
        cache.clear()
        xml = '<?xml version="1.0"?><rss>' + '<item>test</item>' * 300 + '</rss>'
        factory = RequestFactory()
        with patch('EcommerceApp.views_feed._feed_items', return_value=[]), patch(
            'EcommerceApp.views_feed.render_to_string', return_value=xml) as render:
            for encoding in ('gzip', 'identity'):
                request = factory.get('/facebook-feed.xml', HTTP_ACCEPT_ENCODING=encoding)
                response = facebook_feed(request)
                self.assertIn('Accept-Encoding', response['Vary'])
                self.assertEqual(response.status_code, 200)
                if encoding == 'gzip':
                    self.assertEqual(gzip.decompress(response.content).decode(), xml)
                    self.assertLess(len(response.content), len(xml) // 4)
                else:
                    self.assertNotIn('Content-Encoding', response)
                    self.assertEqual(response.content.decode(), xml)
                conditional = factory.get('/facebook-feed.xml', HTTP_ACCEPT_ENCODING=encoding,
                    HTTP_IF_NONE_MATCH=response['ETag'])
                cached = facebook_feed(conditional)
                self.assertEqual(cached.status_code, 304)
                self.assertEqual(cached.content, b'')
            self.assertEqual(render.call_count, 2)


class ReportTests(SimpleTestCase):
    def test_window_aggregation_sources_sampling_and_invalid_lines(self):
        rows = []
        for route, size, source in [('/large/', 100, 'response_body'),
            ('/large/', 200, 'file_content_length'), ('/small/', 5, 'stream_yielded'),
            ('/unknown/', None, 'file_content_length')]:
            rows.append(dict(event='http_bandwidth', schema_version=1,
                timestamp='2026-10-31T12:00:00+00:00', host='example.com', route=route,
                body_bytes=size, byte_source=source, complete=False, status=200,
                ua_category='browser_claimed', sample_rate=0.5))
        old = dict(rows[0], timestamp='2026-10-20T12:00:00+00:00')
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.jsonl') as f:
            f.write('invalid secret line\n')
            for row in rows + [old]:
                f.write('Render-prefix ' + json.dumps(row) + '\n')
            f.flush()
            out = io.StringIO()
            call_command('http_bandwidth_report', f.name, until='2026-10-31T13:00:00+00:00', stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual(result['observed_requests'], 4)
        self.assertEqual(result['observed_body_bytes'], 305)
        first = result['top_by_bytes'][0]
        self.assertEqual(first['route'], '/large/')
        self.assertEqual(first['average_body_bytes'], 150)
        self.assertEqual(first['file_length_estimates'], 1)
        self.assertEqual(result['ignored_lines'], 1)
        self.assertEqual(result['sample_rates'], [0.5])
        self.assertNotIn('secret', out.getvalue())


# Minimal endpoints exercise the actual Django middleware chain without private DB data.
from django.urls import path


def _private_fixture(request, token):
    response = HttpResponse('private response ' * 100)
    response['Cache-Control'] = 'private, no-store'
    response.set_cookie('sessionid', 'fixture')
    return response


urlpatterns = [path('private/<str:token>/', _private_fixture)]


@override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=['testserver'],
    HTTP_DIAGNOSTICS_ENABLED=True, HTTP_DIAGNOSTICS_SAMPLE_RATE=1,
    MIDDLEWARE=['EcommerceApp.middleware.http_diagnostics.HttpDiagnosticsMiddleware',
        'EcommerceApp.middleware.gzip_html.HtmlGZipMiddleware'])
class MiddlewareIntegrationTests(SimpleTestCase):
    def test_private_headers_preserved_and_outer_counter_sees_gzip(self):
        with self.assertLogs('EcommerceApp.http_bandwidth') as logs:
            response = self.client.get('/private/secret-token/?email=private@example.com',
                HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response.cookies['sessionid'].value, 'fixture')
        self.assertEqual(response['Content-Encoding'], 'gzip')
        row = json.loads(logs.records[0].message)
        self.assertEqual(row['body_bytes'], len(response.content))
        self.assertEqual(row['route'], '/private/<str:token>/')
        self.assertNotIn('secret-token', logs.records[0].message)
        self.assertNotIn('@', logs.records[0].message)
