import logging

from EcommerceApp.live_visitors import should_track_visitor, track_live_visitor

logger = logging.getLogger(__name__)


class LiveVisitorMiddleware:
    """Evidentira aktivnost posjetilaca za uzivo analitiku."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Kreiraj sesiju PRIJE rendera da visitor-presence.js ima session_key u HTML-u
        try:
            if should_track_visitor(request) and not request.session.session_key:
                request.session.save()
        except Exception:
            logger.exception('Live visitor session bootstrap failed')

        response = self.get_response(request)
        try:
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