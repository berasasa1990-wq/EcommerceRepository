from django import template
from EcommerceApp.warehouse_access import can_access_route

register = template.Library()
register.filter('warehouse_can', can_access_route)

@register.filter
def warehouse_locked(user, name):
    from EcommerceApp.warehouse_access import warehouse_user_required
    try:
        from EcommerceApp.warehouse_access import inventory_route_locked
    except ImportError:
        from EcommerceApp.bera_panel_modules import module_locked
        return warehouse_user_required(user) and module_locked('pracenje_lagera')
    return warehouse_user_required(user) and inventory_route_locked(name)

@register.filter
def panel_tool_locked(name):
    from django.urls import reverse, NoReverseMatch
    try:
        reverse(name)
    except NoReverseMatch:
        return True
    try:
        from EcommerceApp.bera_panel_modules import panel_route_module, module_locked
        return module_locked(panel_route_module(name))
    except ImportError:
        return False


@register.simple_tag
def inventory_tracking_disabled():
    try:
        from EcommerceApp.module_settings import inventory_mode
        return inventory_mode() is False
    except ImportError:
        return False


register.simple_tag(panel_tool_locked, name='panel_tool_locked')


@register.simple_tag(takes_context=True)
def storefront_availability_visible(context):
    request = context.get('request')
    if request is not None and hasattr(request, '_wms_availability_visible'):
        return request._wms_availability_visible
    try:
        from EcommerceApp.models import WMSSettings
        from EcommerceApp.bera_panel_modules import module_locked
    except ImportError:
        return True
    from EcommerceApp.request_performance import schema_tables
    if WMSSettings._meta.db_table not in schema_tables():
        return True
    visible = not module_locked('wms_zalihe') and not WMSSettings.objects.filter(pk=1, show_storefront_availability=False).exists()
    if request is not None:
        request._wms_availability_visible = visible
    return visible
