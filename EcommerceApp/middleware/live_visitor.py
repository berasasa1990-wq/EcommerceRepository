import logging

from EcommerceApp.live_visitors import track_live_visitor

logger = logging.getLogger(__name__)


class LiveVisitorMiddleware:
    """Evidentira aktivnost posjetilaca za uzivo analitiku."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            track_live_visitor(request)
        except Exception:
            logger.exception('Live visitor tracking failed')
        return response