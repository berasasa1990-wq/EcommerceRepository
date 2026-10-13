import gzip
from pathlib import Path
from django.test import TestCase, RequestFactory
from django.http import HttpResponse
from django.test.utils import CaptureQueriesContext
from django.db import connection
from .request_performance import RequestReadMiddleware
from .utils.seo import get_page_seo
from .models import PageSEO
from scripts.build_home_css import ROOT, BUNDLES, bundle_content
from scripts.css_safe_minify import minify_css


class HomepageOptimizationTests(TestCase):
    def test_bundles_preserve_all_source_bytes_in_order_and_reduce_requests(self):
        for name, sources in BUNDLES.items():
            content = (ROOT / name).read_bytes()
            self.assertEqual(content.decode(), minify_css(bundle_content(sources).decode()))
            self.assertLessEqual(len(gzip.compress(content)), sum(len(gzip.compress((ROOT / source).read_bytes())) for source in sources))
            self.assertNotIn(b'@import', content)
        for font in (ROOT.parent / 'fonts').glob('*.woff2'):
            if 'inter-' in font.name:
                self.assertIn(font.name.encode(), (ROOT / 'home-core.bundle.css').read_bytes())
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('/css/home-first-screen.bundle.css', html)
        self.assertIn('/css/home-core-rest.bundle.css', html)
        self.assertIn('/css/home-ui.bundle.css', html)
        self.assertNotIn('/css/style.min.css', html)
        self.assertNotIn('/css/wishlist.css', html)
        self.assertLess(html.index('home-first-screen.bundle.css'), html.index('id="site-theme"') if 'id="site-theme"' in html else html.index('id="site-orange"'))
        filtered = self.client.get('/', {'q':'nepostojeci'}).content.decode()
        self.assertIn('/css/style.min.css', filtered)
        self.assertNotIn('/css/home-core.bundle.css', filtered)

    def test_seo_read_is_shared_within_request_and_fresh_next_request(self):
        seo, _ = PageSEO.objects.update_or_create(page_key='home', defaults={'seo_title':'Prvo'})
        seen = []
        def view(request):
            seen.append(get_page_seo('home').seo_title)
            self.assertEqual(get_page_seo('home').pk, seo.pk)
            return HttpResponse('OK')
        middleware = RequestReadMiddleware(view)
        with CaptureQueriesContext(connection) as queries:
            middleware(RequestFactory().get('/'))
        self.assertEqual(len(queries), 1)
        PageSEO.objects.filter(pk=seo.pk).update(seo_title='Novo')
        middleware(RequestFactory().get('/'))
        self.assertEqual(seen, ['Prvo', 'Novo'])
