"""Existing model editors mounted inside the staff settings workspace."""
from functools import wraps
from urllib.parse import urlencode

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin


def staff_access(request):
    user = request.user
    return user.is_authenticated and user.is_active and (user.is_staff or user.is_superuser)


class StaffModelPermissions(admin.ModelAdmin):
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self.model._meta.model_name == 'userprofile':
            queryset = queryset.filter(user__is_superuser=False)
        return queryset

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if self.model._meta.model_name == 'userprofile' and db_field.name == 'user':
            kwargs['queryset'] = db_field.remote_field.model.objects.filter(is_superuser=False)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_module_permission(self, request):
        return staff_access(request) if self.model._meta.app_label == 'EcommerceApp' else super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return staff_access(request) if self.model._meta.app_label == 'EcommerceApp' else super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return staff_access(request) if self.model._meta.app_label == 'EcommerceApp' else super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return staff_access(request) if self.model._meta.app_label == 'EcommerceApp' else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return staff_access(request) if self.model._meta.app_label == 'EcommerceApp' else super().has_delete_permission(request, obj)


class StaffInlinePermissions(admin.options.InlineModelAdmin):
    def has_view_permission(self, request, obj=None):
        return staff_access(request)

    def has_add_permission(self, request, obj=None):
        return staff_access(request)

    def has_change_permission(self, request, obj=None):
        return staff_access(request)

    def has_delete_permission(self, request, obj=None):
        return staff_access(request)


class PanelAdminSite(admin.AdminSite):
    site_header = 'Podešavanja'
    site_title = 'Panel'
    index_title = 'Sekcije webshopa'

    def get_app_list(self, request, app_label=None):
        hidden = {'MagacinDeklaracijaBrend', 'WarehouseSupplier',
                  'WarehouseMovement', 'WarehouseStock', 'StaffSiteEvent', 'Order', 'HomeVlog'}
        apps = super().get_app_list(request, app_label)
        for app in apps:
            app['models'] = [model for model in app['models'] if model['object_name'] not in hidden]
        return [app for app in apps if app['models']]

    def has_permission(self, request):
        return staff_access(request)

    def admin_view(self, view, cacheable=False):
        protected = super().admin_view(view, cacheable=cacheable)
        @wraps(view)
        def panel_view(request, *args, **kwargs):
            request.current_app = self.name
            return protected(request, *args, **kwargs)
        return xframe_options_sameorigin(panel_view)

    def login(self, request, extra_context=None):
        if self.has_permission(request):
            return redirect(reverse('staff_settings'))
        next_url = request.GET.get('next') or reverse('staff_settings')
        return redirect(reverse('login') + '?' + urlencode({'next': next_url}))

    def each_context(self, request):
        context = super().each_context(request)
        context['panel_is_embedded'] = True
        return context


panel_admin_site = PanelAdminSite(name='panel_admin')


def install_panel_editors():
    """Reuse registrations and move webshop model editors out of /admin/."""
    for model, editor in list(admin.site._registry.items()):
        if model._meta.model_name == 'modulepermissions':
            continue
        if model in panel_admin_site._registry:
            continue
        if model._meta.app_label == 'EcommerceApp':
            inlines = [type('Panel' + inline.__name__, (inline, StaffInlinePermissions), {})
                       for inline in editor.inlines]
            cls = type('Panel' + editor.__class__.__name__,
                       (editor.__class__, StaffModelPermissions), {'inlines': inlines})
            panel_admin_site.register(model, cls)
            admin.site.unregister(model)
        elif model._meta.app_label == 'auth':
            # Account edits remain superuser-only. User autocomplete can read
            # existing accounts for customer relations without editing auth users.
            def can_view_accounts(self, request, obj=None):
                return request.user.is_superuser if obj is not None else staff_access(request)
            def search_accounts(self, request, queryset, search_term):
                if (request.GET.get('app_label') == 'EcommerceApp'
                        and request.GET.get('model_name') == 'userprofile'
                        and request.GET.get('field_name') == 'user'):
                    queryset = queryset.filter(is_superuser=False)
                return super(type(self), self).get_search_results(request, queryset, search_term)
            cls = type('Panel' + editor.__class__.__name__, (editor.__class__,),
                       {'has_view_permission': can_view_accounts, 'get_search_results': search_accounts})
            panel_admin_site.register(model, cls)
