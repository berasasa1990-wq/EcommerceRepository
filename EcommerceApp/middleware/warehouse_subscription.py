from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from EcommerceApp.warehouse_access import can_access_route, route_features


class WarehouseSubscriptionMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        name = request.resolver_match.url_name or ''
        if not route_features(name) or not request.user.is_authenticated:
            return None
        if not can_access_route(request.user, name):
            return render(request, 'account/magacin_access_denied.html', status=403)
        return None
