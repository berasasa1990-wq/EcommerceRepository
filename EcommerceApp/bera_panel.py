"""BERA panel landing using the destination's existing admin and permissions."""
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .bera_home import storefront_context
from .models import Order

B2B_KEYS = ('b2bsettings', 'b2baccount', 'b2bsubmission', 'b2blive')
PRODUCT_KEYS = ('product', 'homefeaturedproduct', 'homenovoproduct', 'homebestsellerproduct', 'tag', 'category', 'brand')
PROMOTION_KEYS = ('akcija', 'upselloffer')
SETTINGS_KEYS = ('banner', 'userprofile', 'pageseo')
WMS_KEYS = ('warehousecustomer', 'warehouselocation', 'productwarehousemeta', 'warehousesynclog')


def can_open_panel(user):
    return bool(user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser))


def model_sections(request):
    from .bera_panel_settings import panel_model_sections
    return panel_model_sections(request)


@login_required(login_url='login')
@user_passes_test(can_open_panel, login_url='login')
@require_GET
def staff_panel(request):
    from .views import _base_context
    sections = model_sections(request)
    excluded = (*B2B_KEYS, *WMS_KEYS, *PRODUCT_KEYS[1:], *PROMOTION_KEYS[1:], *SETTINGS_KEYS, 'loyaltycard', 'sitesettings')
    return render(request, 'bera_reference/staff/panel.html', {
        **_base_context(), **storefront_context(),
        'is_superuser_staff': request.user.is_superuser,
        'new_orders_count': Order.objects.filter(status=Order.Status.NOVA).count() if request.user.is_superuser else 0,
        'model_sections': [item for item in sections if item['key'] not in excluded],
        'panel_b2b_url': next((item['workspace_url'] for item in sections if item['key'] == 'b2baccount'), ''),
        'panel_settings_url': next((item['workspace_url'] for item in sections if item['key'] == 'sitesettings'), ''),
    })
