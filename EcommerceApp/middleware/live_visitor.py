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
        # Kreiraj sesiju PRIJE rendera da visitor-presence.js ima session_key u HTML-u
        try:
            if should_track_visitor(request) and not request.session.session_key:
                request.session.save()
        except Exception:
            logger.exception('Live visitor session bootstrap failed')

        # Track ODMAH na GET stranicama — staff live vidi kupca čim stigne request
        # (ne čeka kraj rendera HTML-a)
        tracked_early = False
        try:
            path = getattr(request, 'path', '') or ''
            if (
                should_track_visitor(request)
                and request.method in ('GET', 'HEAD')
                and not is_background_request_path(path)
            ):
                track_live_visitor(request)
                tracked_early = True
        except Exception:
            logger.exception('Live visitor early tracking failed')

        response = self.get_response(request)
        try:
            # POST / ostalo — ili ako early track nije uspio
            if not tracked_early:
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