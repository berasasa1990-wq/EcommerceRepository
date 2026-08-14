from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse


class SitePrepLockMiddleware:
    """Blokira javni pristup sajtu dok je u pripremi (lozinka u sesiji)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, 'SITE_PREP_ENABLED', False):
            return self.get_response(request)

        password = getattr(settings, 'SITE_PREP_PASSWORD', '')
        if not password:
            return self.get_response(request)

        if self._is_exempt(request.path):
            return self.get_response(request)

        if getattr(request.user, 'is_staff', False):
            return self.get_response(request)

        session_key = getattr(settings, 'SITE_PREP_SESSION_KEY', 'site_prep_unlocked')
        if request.session.get(session_key):
            return self.get_response(request)

        unlock_url = reverse('site_prep_unlock')
        if request.path == unlock_url:
            return self.get_response(request)

        next_url = quote(request.get_full_path(), safe='')
        return HttpResponseRedirect(f'{unlock_url}?next={next_url}')

    def _is_exempt(self, path):
        static_url = settings.STATIC_URL
        media_url = settings.MEDIA_URL or ''
        if static_url and path.startswith(static_url):
            return True
        # Lokalni /media/ (kad R2 nije aktivan). Vanjski https://media… se ne matcha na path.
        if media_url.startswith('/') and path.startswith(media_url):
            return True
        if path.startswith('/media/'):
            return True
        if path.startswith('/admin/'):
            return True
        # Prijava/odjava moraju raditi i dok je sajt u pripremi — inače loop na login.
        if path.startswith((
            '/prijava/',
            '/registracija/',
            '/odjava/',
            '/activate/',
        )):
            return True
        if path in ('/facebook-feed.xml', '/healthz', '/healthz/'):
            return True
        if path.startswith('/healthz'):
            return True
        # AJAX endpoints (JSON) — ne smiju vratiti HTML redirect
        if path.startswith('/online-nagrada/'):
            return True
        if path.startswith('/ponuda/'):
            return True
        if path.startswith('/uzivo/'):
            return True
        # Javna slika loyalty kartice (link iz WhatsApp/Viber poruke)
        if path.startswith('/loyalty/kartica/'):
            return True
        return False