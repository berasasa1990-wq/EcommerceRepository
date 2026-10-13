"""DB-free frozen storefront: raw static (8093) versus collected WhiteNoise (8094).
Run with venv Python. Requires /tmp/local-mobile-home.html and QA collectstatic.
Only GET/HEAD; no Django setup, models, session processing or integrations.
"""
import argparse
import gzip
import json
import re
import threading
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from whitenoise import WhiteNoise

ROOT = Path(__file__).resolve().parents[1]
QA = Path('/private/tmp/carp-mobile-static-qa')

class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass

def make_app(snapshot, compressed):
    port = 8094 if compressed else 8093
    html = snapshot.replace('http://127.0.0.1:8000', f'http://127.0.0.1:{port}')
    if compressed:
        manifest = json.loads((QA / 'collected/staticfiles.json').read_text())['paths']
        html = re.sub(r'/static/([^\s"\'<>?]+)', lambda m: '/static/' + manifest.get(m[1], m[1]), html)
    plain = html.encode()
    body = gzip.compress(plain)
    def application(environ, start_response):
        if environ['REQUEST_METHOD'] not in ('GET', 'HEAD'):
            start_response('405 Method Not Allowed', [('Content-Length', '0')])
            return [b'']
        if environ['PATH_INFO'] == '/':
            compressed_html = 'gzip' in environ.get('HTTP_ACCEPT_ENCODING', '')
            payload = body if compressed_html else plain
            headers = [('Content-Type', 'text/html; charset=utf-8'),
                ('Content-Length', str(len(payload))), ('Cache-Control', 'no-store'),
                ('Vary', 'Accept-Encoding')]
            if compressed_html:
                headers.append(('Content-Encoding', 'gzip'))
            start_response('200 OK', headers)
            return [] if environ['REQUEST_METHOD'] == 'HEAD' else [payload]
        start_response('404 Not Found', [('Content-Length', '0')])
        return [b'']
    media = WhiteNoise(application, root=ROOT / 'media', prefix='/media/', autorefresh=True, max_age=31536000)
    return WhiteNoise(media, root=(QA / 'collected' if compressed else ROOT / 'EcommerceApp/static'),
        prefix='/static/', max_age=31536000 if compressed else None,
        immutable_file_test=(r'\.[0-9a-f]{12}\.' if compressed else None))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--html', default='/private/tmp/local-mobile-home.html')
    args = parser.parse_args()
    snapshot = Path(args.html).read_text()
    servers = [make_server('127.0.0.1', port, make_app(snapshot, port == 8094),
        server_class=ThreadedServer, handler_class=QuietHandler) for port in (8093, 8094)]
    try:
        for server in servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        print('DB-free snapshots ready: raw :8093; compressed/cache :8094', flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()

if __name__ == '__main__':
    main()
