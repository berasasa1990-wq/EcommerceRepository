from django.conf import settings
from django.views.static import serve as static_serve


def serve_media(request, path):
    """Serve uploaded media with long-lived cache headers for repeat visits."""
    response = static_serve(request, path, document_root=settings.MEDIA_ROOT)
    if response.status_code == 200:
        max_age = getattr(settings, 'MEDIA_CACHE_MAX_AGE', 31536000)
        response['Cache-Control'] = f'public, max-age={max_age}, immutable'
    return response