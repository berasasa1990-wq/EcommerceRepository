import logging

from EcommerceApp.live_visitors import (
    is_background_request_path,
    should_track_visitor,
    track_live_visitor,
)

logger = logging.getLogger(__name__)


class LiveVisitorMiddleware:
    """Evidentira aktivnost posjetilaca za uzivo analitiku."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = getattr(request, 'path', '') or ''
        # Sitemap / robots / health — nula tracking overheada
        if path.startswith(('/sitemap', '/robots.txt', '/healthz', '/favicon', '/static/', '/media/')):
            return self.get_response(request)

        # Staff/superuser: zapamti IP da se ne broji u analyticsu (ni kad nije ulogovan)
        try:
            from EcommerceApp.live_visitors import remember_owner_ip

            user = getattr(request, 'user', None)
            if user and getattr(user, 'is_authenticated', False) and (
                user.is_superuser or user.is_staff
            ):
                remember_owner_ip(request)
        except Exception:
            logger.exception('Live visitor owner IP remember failed')

        # Kreiraj sesiju PRIJE rendera da visitor-presence.js ima session_key u HTML-u
        try:
            if should_track_visitor(request) and not request.session.session_key:
                request.session.save()
        except Exception:
            logger.exception('Live visitor session bootstrap failed')

        response = self.get_response(request)
        try:
            # Broji samo uspješno učitane stranice, nikad 404, slike ili API odgovore.
            if (
                request.method == 'GET'
                and 200 <= response.status_code < 300
                and response.get('Content-Type', '').split(';', 1)[0].strip().lower() == 'text/html'
                and request.headers.get('X-Requested-With') != 'XMLHttpRequest'
                and should_track_visitor(request)
                and not is_background_request_path(path)
            ):
                track_live_visitor(request)
        except Exception:
            logger.exception('Live visitor tracking failed')
        # Trajni cookie za vraćene posjetioce (nije prvi put na sajtu)
        try:
            token = getattr(request, '_ozb_vid_set', None)
            if token:
                from EcommerceApp.live_visitors import VISITOR_COOKIE, VISITOR_COOKIE_MAX_AGE

                response.set_cookie(
                    VISITOR_COOKIE,
                    token,
                    max_age=VISITOR_COOKIE_MAX_AGE,
                    samesite='Lax',
                    httponly=True,
                    secure=request.is_secure(),
                    path='/',
                )
        except Exception:
            logger.exception('Live visitor cookie failed')
        return response