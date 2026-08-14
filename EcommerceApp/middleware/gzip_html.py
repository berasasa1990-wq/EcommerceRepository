from django.middleware.gzip import GZipMiddleware


class HtmlGZipMiddleware(GZipMiddleware):
    """Gzip samo HTML. JSON pollovi su mali — kompresija samo troši CPU."""

    def process_response(self, request, response):
        content_type = (response.get('Content-Type') or '').split(';', 1)[0].strip().lower()
        if content_type and content_type != 'text/html':
            return response
        return super().process_response(request, response)
