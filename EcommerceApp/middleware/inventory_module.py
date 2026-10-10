from django.http import JsonResponse
from django.shortcuts import render

from EcommerceApp.warehouse_access import inventory_route_locked


class InventoryModuleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if not match:
            return None
        from EcommerceApp.panel_modules import panel_route_module, module_locked
        name = match.url_name or getattr(view_func, '__name__', '')
        panel_field = panel_route_module(name, view_kwargs.get('section'))
        if not inventory_route_locked(name) and not module_locked(panel_field):
            return None
        from EcommerceApp.models import ModulePermissions
        from EcommerceApp.warehouse_access import warehouse_module_field
        field = panel_field or warehouse_module_field(name)
        module = ModulePermissions._meta.get_field(field).verbose_name if field else 'Praćenje lagera'
        message = f'Ovaj alat je zaključan jer je modul {module} isključen.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'ok': False, 'error': message}, status=403)
        from django.urls import reverse
        return render(request, 'staff/magacin/module_locked.html', {
            'lock_message': message, 'locked_module': module,
            'modules_url': reverse('admin:EcommerceApp_modulepermissions_changelist', current_app='admin'),
        }, status=403)
