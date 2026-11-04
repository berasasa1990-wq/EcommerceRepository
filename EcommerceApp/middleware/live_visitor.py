"""Compatibility middleware for old imports; Live Visitors tracking is retired."""


class LiveVisitorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)
