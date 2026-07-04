import uuid

from EcommerceApp.meta_conversions import track_page_view


class MetaPageViewMiddleware:
    """Server-side PageView for Meta Conversions API (deduplicated with browser pixel)."""

    SKIP_PREFIXES = (
        '/admin/',
        '/api/',
        '/static/',
        '/media/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.meta_page_view_event_id = None
        if self._should_track(request):
            event_id = f'pageview-{uuid.uuid4().hex}'
            request.meta_page_view_event_id = event_id
            track_page_view(request, event_id=event_id)
        return self.get_response(request)

    def _should_track(self, request):
        if request.method != 'GET':
            return False
        path = request.path
        if path == '/facebook-feed.xml':
            return False
        return not any(path.startswith(prefix) for prefix in self.SKIP_PREFIXES)