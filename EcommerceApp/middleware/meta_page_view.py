import uuid

from EcommerceApp.live_visitors import is_background_request_path
from EcommerceApp.meta_conversions import track_page_view


class MetaPageViewMiddleware:
    """Server-side PageView for Meta Conversions API (deduplicated with browser pixel)."""

    SKIP_PREFIXES = (
        '/admin/',
        '/api/',
        '/static/',
        '/media/',
        '/nalog/',
        '/sitemap',
        '/robots.txt',
        '/favicon',
        '/healthz',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.meta_page_view_event_id = None
        if self._should_track(request):
            event_id = f'pageview-{uuid.uuid4().hex}'
            request.meta_page_view_event_id = event_id
        response = self.get_response(request)
        if (
            request.meta_page_view_event_id
            and 200 <= response.status_code < 300
            and response.get('Content-Type', '').split(';', 1)[0].strip().lower() == 'text/html'
        ):
            track_page_view(request, event_id=request.meta_page_view_event_id)
        return response

    def _should_track(self, request):
        if request.method != 'GET':
            return False
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return False
        path = request.path or ''
        if path == '/facebook-feed.xml':
            return False
        if is_background_request_path(path):
            return False
        return not any(path.startswith(prefix) for prefix in self.SKIP_PREFIXES)
