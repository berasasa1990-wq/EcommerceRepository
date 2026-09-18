from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from .middleware.permissions_policy import PermissionsPolicyMiddleware

EXPECTED_POLICY = 'geolocation=(), microphone=(), payment=(), camera=(self), fullscreen=(self)'


class PermissionsPolicyMiddlewareTests(SimpleTestCase):
    def test_preserves_existing_headers_without_duplicates(self):
        response = HttpResponse(headers={
            'permissions-policy': 'camera=()',
            'Content-Security-Policy': "default-src 'self'",
            'Strict-Transport-Security': 'max-age=1234',
            'Referrer-Policy': 'same-origin',
            'X-Frame-Options': 'DENY',
            'X-Content-Type-Options': 'nosniff',
        })
        before = dict(response.headers)
        result = PermissionsPolicyMiddleware(lambda request: response)(RequestFactory().get('/'))
        self.assertEqual(dict(result.headers), before)
        self.assertEqual(sum(key.lower() == 'permissions-policy' for key in result.headers), 1)

    def test_streaming_download_keeps_body_and_gets_header(self):
        response = StreamingHttpResponse(iter([b'backup content']))
        result = PermissionsPolicyMiddleware(lambda request: response)(RequestFactory().get('/download/'))
        self.assertEqual(result.headers['Permissions-Policy'], EXPECTED_POLICY)
        self.assertEqual(b''.join(result.streaming_content), b'backup content')
        self.assertNotIn('Content-Security-Policy', result.headers)


@override_settings(
    SITE_PREP_ENABLED=False,
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class PermissionsPolicyResponseTests(TestCase):
    def test_site_and_admin_responses_have_policy_without_adding_csp(self):
        for path, status in [('/', 200), ('/healthz/', 200), ('/admin/login/', 200), ('/admin/', 302)]:
            with self.subTest(path=path):
                response = self.client.get(path, secure=True)
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers['Permissions-Policy'], EXPECTED_POLICY)
                self.assertNotIn('Content-Security-Policy', response.headers)
                self.assertNotIn('Content-Security-Policy-Report-Only', response.headers)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_HSTS_SECONDS=31536000,
                       SECURE_HSTS_INCLUDE_SUBDOMAINS=True, SECURE_HSTS_PRELOAD=True,
                       SECURE_CONTENT_TYPE_NOSNIFF=True, SECURE_REFERRER_POLICY='same-origin',
                       X_FRAME_OPTIONS='DENY')
    def test_https_redirect_and_existing_security_headers_are_preserved(self):
        redirect = self.client.get('/healthz/')
        self.assertEqual(redirect.status_code, 301)
        self.assertEqual(redirect.headers['Permissions-Policy'], EXPECTED_POLICY)
        response = self.client.get('/healthz/', secure=True)
        self.assertEqual(response.headers['Permissions-Policy'], EXPECTED_POLICY)
        self.assertEqual(response.headers['Strict-Transport-Security'], 'max-age=31536000; includeSubDomains; preload')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(response.headers['Referrer-Policy'], 'same-origin')
