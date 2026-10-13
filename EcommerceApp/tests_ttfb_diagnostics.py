from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from .middleware.ttfb_diagnostics import TTFBDiagnosticsMiddleware, Timings, measure_connections


class FakeDatabase:
    def get_new_connection(self, params):
        return object()

    @contextmanager
    def execute_wrapper(self, wrapper):
        self.wrapper = wrapper
        try:
            yield
        finally:
            self.wrapper = None


@override_settings(TTFB_DIAGNOSTICS_ENABLED=True)
class TTFBDiagnosticsTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get('/artikal/customer-secret/?token=private-secret',
                                            HTTP_X_REQUEST_ID='untrusted-secret')
        self.request.resolver_match = SimpleNamespace(url_name='product_detail')
        self.database = FakeDatabase()

    def invoke(self, handler, clock):
        with patch('EcommerceApp.middleware.ttfb_diagnostics.connections.all', return_value=[self.database]), \
             patch('EcommerceApp.middleware.ttfb_diagnostics.perf_counter', side_effect=clock):
            return TTFBDiagnosticsMiddleware(handler)(self.request)

    def test_aggregate_headers_connection_and_slow_log_without_secrets(self):
        def response(request):
            self.database.wrapper(lambda *args: None, 'SELECT private-sql', ['private-param'], False, {})
            self.database.get_new_connection({'password': 'private-password'})
            return HttpResponse('original body', headers={'Server-Timing': 'existing;dur=2'})
        with self.assertLogs('EcommerceApp.middleware.ttfb_diagnostics', level='WARNING') as logs:
            result = self.invoke(response, [0, .1, .2, .3, .5, 1.5])
        self.assertEqual(result.content, b'original body')
        self.assertIn('existing;dur=2, django;dur=1500.00', result['Server-Timing'])
        self.assertIn('sql;dur=100.00;desc="1 queries"', result['Server-Timing'])
        self.assertIn('db_connect;dur=200.00;desc="1 attempts"', result['Server-Timing'])
        self.assertIn('sql_max;dur=100.00', result['Server-Timing'])
        request_id = result['X-TTFB-Request-ID']
        UUID(hex=request_id)
        self.assertIn('request_id=' + request_id, logs.output[0])
        self.assertIn('route=product_detail', logs.output[0])
        for secret in ('customer-secret', 'private-secret', 'untrusted-secret', 'private-sql', 'private-param', 'private-password'):
            self.assertNotIn(secret, str(logs.output) + str(dict(result.headers)))
        self.assertNotIn('get_new_connection', self.database.__dict__)

    def test_longest_query_and_slow_count_include_failed_queries_without_sql_text(self):
        timings = Timings()
        def fail(*args):
            raise RuntimeError('driver failure')
        with patch('EcommerceApp.middleware.ttfb_diagnostics.perf_counter',
                   side_effect=[0, .05, 1, 1.2, 2, 2.4]):
            timings.execute(lambda *args: None, 'SELECT private-one', ['private-param'], False, {})
            timings.execute(lambda *args: None, 'SELECT private-two', [], False, {})
            with self.assertRaises(RuntimeError):
                timings.execute(fail, 'SELECT private-three', [], False, {})
        self.assertEqual(timings.sql_count, 3)
        self.assertAlmostEqual(timings.sql_seconds, .65)
        self.assertAlmostEqual(timings.sql_max_seconds, .4)
        self.assertEqual(timings.slow_sql_count, 2)
        self.assertNotIn('private', str(timings))

    def test_fast_and_exact_threshold_requests_do_not_log(self):
        for duration in (.02, 1.0):
            with patch('EcommerceApp.middleware.ttfb_diagnostics.logger.warning') as log:
                response = self.invoke(lambda r: HttpResponse('ok'), [0, duration])
                log.assert_not_called()
                self.assertIn('0 queries', response['Server-Timing'])
                self.assertIn('0 attempts', response['Server-Timing'])

    @override_settings(TTFB_DIAGNOSTICS_ENABLED=False)
    def test_disabled_does_not_instrument_or_change_headers(self):
        with patch('EcommerceApp.middleware.ttfb_diagnostics.connections.all') as db:
            result = TTFBDiagnosticsMiddleware(lambda r: HttpResponse('ok'))(self.request)
            db.assert_not_called()
            self.assertNotIn('Server-Timing', result)
            self.assertNotIn('X-TTFB-Request-ID', result)

    def test_private_static_and_write_requests_are_excluded(self):
        for path in ('/wms', '/wms/narudzbe/', '/panel/', '/nalog/', '/admin/', '/api/products/', '/static/a.css', '/media/a.jpg', '/veleprodaja/korpa/'):
            with patch('EcommerceApp.middleware.ttfb_diagnostics.connections.all') as db:
                result = TTFBDiagnosticsMiddleware(lambda r: HttpResponse('ok'))(RequestFactory().get(path))
                db.assert_not_called()
                self.assertNotIn('Server-Timing', result)
        result = TTFBDiagnosticsMiddleware(lambda r: HttpResponse('ok'))(RequestFactory().post('/'))
        self.assertNotIn('Server-Timing', result)

    def test_restores_connection_wrapper_when_request_fails(self):
        def failure(request):
            raise RuntimeError('expected')
        with self.assertRaises(RuntimeError):
            self.invoke(failure, [0])
        self.assertNotIn('get_new_connection', self.database.__dict__)
        self.assertIsNone(self.database.wrapper)

    def test_connection_failure_is_measured_and_original_instance_method_restored(self):
        def failure(params):
            raise RuntimeError('connection failed')
        self.database.get_new_connection = failure
        timings = Timings()
        with patch('EcommerceApp.middleware.ttfb_diagnostics.perf_counter', side_effect=[0, .4]):
            with measure_connections(self.database, timings):
                with self.assertRaises(RuntimeError):
                    self.database.get_new_connection({})
        self.assertIs(self.database.get_new_connection, failure)
        self.assertEqual(timings.connect_count, 1)
        self.assertAlmostEqual(timings.connect_seconds, .4)


from django.test import TestCase
from .models import Product


@override_settings(TTFB_DIAGNOSTICS_ENABLED=True)
class TTFBDatabaseIntegrationTests(TestCase):
    def test_real_sql_is_counted_without_opening_an_extra_connection(self):
        def view(request):
            Product.objects.count()
            return HttpResponse('ok')
        response = TTFBDiagnosticsMiddleware(view)(RequestFactory().get('/'))
        self.assertIn('1 queries', response['Server-Timing'])
        self.assertIn('0 attempts', response['Server-Timing'])

    def test_public_health_headers_and_private_account_exclusion(self):
        response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
        self.assertIn('django;dur=', response['Server-Timing'])
        self.assertIn('X-TTFB-Request-ID', response)
        response = self.client.get('/nalog/')
        self.assertNotIn('X-TTFB-Request-ID', response)
