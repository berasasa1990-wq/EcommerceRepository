"""Read-only harness regression: no Django setup or DB required."""
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
import mobile_static_snapshot_server as server

class SnapshotSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for folder in ('EcommerceApp/static', 'media', 'qa/collected'):
            (self.root / folder).mkdir(parents=True)
        self.css = b'body{color:blue}' * 100
        (self.root / 'EcommerceApp/static/style.css').write_bytes(self.css)
        collected = self.root / 'qa/collected'
        (collected / 'style.0123456789ab.css').write_bytes(self.css)
        (collected / 'style.0123456789ab.css.gz').write_bytes(gzip.compress(self.css))
        (collected / 'staticfiles.json').write_text(json.dumps({'paths': {'style.css': 'style.0123456789ab.css'}}))
        self.html = '<link href="/static/style.css"><a href="http://127.0.0.1:8000/">Test</a>'

    def app(self, compressed):
        with patch.object(server, 'ROOT', self.root), patch.object(server, 'QA', self.root / 'qa'):
            return server.make_app(self.html, compressed)

    def request(self, app, path='/', method='GET', accept='gzip'):
        env = {}; setup_testing_defaults(env)
        env.update(PATH_INFO=path, REQUEST_METHOD=method, HTTP_ACCEPT_ENCODING=accept)
        response = {}
        def start(status, headers):
            response.update(status=status, headers=dict(headers))
        stream = app(env, start)
        try:
            response['body'] = b''.join(stream)
        finally:
            if hasattr(stream, 'close'): stream.close()
        return response

    def test_no_write_methods_or_backend_routes(self):
        app = self.app(True)
        self.assertTrue(self.request(app, method='POST')['status'].startswith('405'))
        self.assertTrue(self.request(app, path='/checkout/')['status'].startswith('404'))

    def test_only_snapshot_urls_and_delivery_change(self):
        r = self.request(self.app(True))
        html = gzip.decompress(r['body']).decode()
        self.assertIn('/static/style.0123456789ab.css', html)
        self.assertIn('http://127.0.0.1:8094/', html)
        self.assertEqual(r['headers']['Cache-Control'], 'no-store')
        self.assertNotIn('Set-Cookie', r['headers'])

    def test_static_compression_preserves_css_and_adds_immutable_cache(self):
        raw = self.request(self.app(False), '/static/style.css')
        compressed = self.request(self.app(True), '/static/style.0123456789ab.css')
        self.assertEqual(raw['body'], gzip.decompress(compressed['body']))
        self.assertNotIn('Cache-Control', raw['headers'])
        self.assertIn('immutable', compressed['headers']['Cache-Control'])
        self.assertEqual(compressed['headers']['Content-Encoding'], 'gzip')

    def test_html_without_encoding_and_head(self):
        app = self.app(False)
        r = self.request(app, accept='')
        self.assertTrue(r['body'].startswith(b'<link'))
        self.assertNotIn('Content-Encoding', r['headers'])
        self.assertEqual(self.request(app, method='HEAD')['body'], b'')

if __name__ == '__main__': unittest.main()
