"""Additional panel editors; keep original admin registrations and permissions."""
from functools import wraps
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from .bera_panel_modules import model_module, module_locked


class PanelAdminSite(admin.AdminSite):
    site_header = 'Podešavanja'
    site_title = 'Panel'
    index_title = 'Sekcije webshopa'
    index_template = 'bera_panel_admin/index.html'
    app_index_template = 'bera_panel_admin/app_index.html'

    def get_app_list(self, request, app_label=None):
        hidden = {'ModulePermissions'}
        apps = super().get_app_list(request, app_label)
        for app in apps:
            app['models'] = [model for model in app['models'] if model['object_name'] not in hidden]
        return [app for app in apps if app['models']]

    def admin_view(self, view, cacheable=False):
        protected = super().admin_view(view, cacheable)
        @wraps(view)
        def panel_view(request, *args, **kwargs):
            owner = getattr(view, '__self__', None)
            model = getattr(owner, 'model', None)
            if model and module_locked(model_module(model._meta.model_name)):
                raise PermissionDenied
            if model and model._meta.model_name == 'product' and request.method == 'POST' and module_locked('wms_zalihe'):
                if request.POST.get('action') == 'off_stock' or any(
                    key in {'stanje', 'na_stanju'} or key.endswith(('-stanje', '-na_stanju'))
                    for key in request.POST
                ):
                    raise PermissionDenied
            request.current_app = self.name
            response = protected(request, *args, **kwargs)
            old_prefix = reverse('admin:index')
            panel_prefix = reverse('panel_admin:index')
            def panel_urls(value):
                if isinstance(value, str) and value.startswith(old_prefix):
                    return panel_prefix + value[len(old_prefix):]
                if isinstance(value, dict):
                    return {key: panel_urls(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [panel_urls(item) for item in value]
                return value
            if response.has_header('Location'):
                response['Location'] = panel_urls(response['Location'])
            if getattr(response, 'context_data', None):
                response.context_data = panel_urls(response.context_data)
            return response
        return xframe_options_sameorigin(panel_view)

    def each_context(self, request):
        from .bera_home import storefront_context
        return {**super().each_context(request), **storefront_context(), 'panel_is_embedded': True}


panel_admin_site = PanelAdminSite(name='panel_admin')


def install_panel_editors():
    from pathlib import Path
    from django.conf import settings
    root = Path(settings.BASE_DIR) / 'EcommerceApp/template/bera_panel_admin'
    for model, editor in list(admin.site._registry.items()):
        if model._meta.model_name == 'modulepermissions':
            continue
        if model in panel_admin_site._registry or model._meta.app_label not in {'EcommerceApp', 'auth'}:
            continue
        options = {}
        original_media = getattr(editor.__class__, 'Media', None)
        custom_js = getattr(original_media, 'js', ())
        if custom_js:
            # Legacy custom scripts use django.jQuery even on list pages.
            options['Media'] = type('PanelMedia', (original_media,), {
                'js': tuple(dict.fromkeys(('admin/js/vendor/jquery/jquery.js', 'admin/js/jquery.init.js', *custom_js))),
            })
        for attribute, fallback in [('change_list_template', 'change_list.html'), ('change_form_template', 'change_form.html'),
                                    ('delete_confirmation_template', 'delete_confirmation.html'),
                                    ('delete_selected_confirmation_template', 'delete_selected_confirmation.html'),
                                    ('object_history_template', 'object_history.html')]:
            original = getattr(editor, attribute, None)
            local = original.removeprefix('admin/') if isinstance(original, str) else fallback
            if (root / local).exists():
                options[attribute] = 'bera_panel_admin/' + local
        bases = (editor.__class__,)
        if model._meta.model_name == 'product':
            from .bera_panel_stock import PanelProductStockMixin
            bases = (PanelProductStockMixin, *bases)
        cls = type('Panel' + editor.__class__.__name__, bases, options)
        panel_admin_site.register(model, cls)
