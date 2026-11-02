"""Opt-in application body accounting. Never logs request data or raw paths.

Counts are NOT socket/network/billing measurements. FileResponse keeps its WSGI
file wrapper; its declared length is only an estimate, including on disconnect.
"""
import json
import logging
import random
import re
import time
from datetime import datetime, timezone

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import FileResponse

logger = logging.getLogger('EcommerceApp.http_bandwidth')


def _route(request):
    match = getattr(request, 'resolver_match', None)
    route = getattr(match, 'route', None)
    if route is not None:
        return '/' + str(route).lstrip('/')
    # Never record arbitrary paths: they can contain emails, tokens or names.
    for prefix in (settings.STATIC_URL, settings.MEDIA_URL):
        if prefix and prefix.startswith('/') and request.path.startswith(prefix):
            return prefix.rstrip('/') + '/*'
    return '(unresolved)'


def _host(request):
    try:
        host = request.get_host().lower()
        return host if re.fullmatch(r'[a-z0-9.\-:\[\]]{1,253}', host) else '(invalid)'
    except Exception:
        return '(invalid)'


def _ua_category(request):
    ua = request.META.get('HTTP_USER_AGENT', '').lower()
    for markers, category in (
        (('googlebot', 'google-inspectiontool'), 'google_claimed'),
        (('bingbot', 'bingpreview'), 'bing_claimed'),
        (('facebookexternalhit', 'meta-external', 'facebot'), 'meta_crawler_claimed'),
        (('instagram', 'fban', 'fbav'), 'meta_browser_claimed'),
        (('bot', 'spider', 'crawler', 'curl/', 'wget/'), 'other_automation_claimed'),
        (('mozilla/',), 'browser_claimed'),
    ):
        if any(marker in ua for marker in markers):
            return category
    return 'other' if ua else 'missing'


class HttpDiagnosticsMiddleware:
    def __init__(self, get_response):
        if not settings.HTTP_DIAGNOSTICS_ENABLED:
            raise MiddlewareNotUsed
        self.get_response = get_response
        self.rate = min(1.0, max(0.0, float(settings.HTTP_DIAGNOSTICS_SAMPLE_RATE)))

    def __call__(self, request):
        if self.rate == 0 or (self.rate < 1 and random.random() >= self.rate):
            return self.get_response(request)
        started = time.monotonic()
        response = self.get_response(request)
        record = {
            'event': 'http_bandwidth', 'schema_version': 1,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'host': _host(request), 'route': _route(request),
            'method': request.method if request.method in
            {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'} else 'OTHER',
            'status': response.status_code, 'ua_category': _ua_category(request),
            'sample_rate': self.rate,
        }
        finished = False

        def emit(size, source, complete):
            nonlocal finished
            if finished:
                return
            finished = True
            try:
                logger.info(json.dumps(dict(record, body_bytes=size, byte_source=source,
                    complete=complete, duration_ms=round((time.monotonic() - started) * 1000, 3)),
                    separators=(',', ':')))
            except Exception:
                # Diagnostics must never interrupt delivery or a resource closer.
                pass

        if request.method == 'HEAD' or response.status_code in (204, 304) or response.status_code < 200:
            emit(0, 'no_body', True)
        elif not response.streaming:
            emit(len(response.content), 'response_body', True)
        elif isinstance(response, FileResponse):
            length = response.get('Content-Length', '')
            size = int(length) if length.isdigit() else None
            response._resource_closers.append(lambda: emit(size, 'file_content_length', None))
        else:
            original = response.streaming_content
            count = 0

            def finish():
                emit(count, 'stream_yielded', False)

            if response.is_async:
                async def chunks():
                    nonlocal count
                    complete = False
                    try:
                        async for chunk in original:
                            count += len(chunk)
                            yield chunk
                        complete = True
                    finally:
                        emit(count, 'stream_yielded', complete)
            else:
                def chunks():
                    nonlocal count
                    complete = False
                    try:
                        for chunk in original:
                            count += len(chunk)
                            yield chunk
                        complete = True
                    finally:
                        emit(count, 'stream_yielded', complete)
            response.streaming_content = chunks()
            response._resource_closers.append(finish)
        return response
