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
        return response