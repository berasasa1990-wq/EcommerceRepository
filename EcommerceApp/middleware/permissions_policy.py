"""Browser capability restrictions that preserve warehouse camera scanners."""
from django.utils.deprecation import MiddlewareMixin


class PermissionsPolicyMiddleware(MiddlewareMixin):
    # Camera scanners and native video fullscreen remain available on this origin.
    # Checkout uses cash on delivery, not the browser Payment Request API.
    policy = 'geolocation=(), microphone=(), payment=(), camera=(self), fullscreen=(self)'

    def process_response(self, request, response):
        # Preserve an explicit policy supplied by a view or another middleware.
        response.headers.setdefault('Permissions-Policy', self.policy)
        return response
