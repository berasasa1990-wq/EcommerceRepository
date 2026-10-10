"""Django model administration is reserved for active superusers."""
from django.contrib.admin import AdminSite


class SuperuserAdminSite(AdminSite):
    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        modules = []
        for app in apps:
            modules.extend(row for row in app['models'] if row['object_name'] == 'ModulePermissions')
            app['models'] = [row for row in app['models'] if row['object_name'] != 'ModulePermissions']
        if modules:
            apps.insert(0, {'name': 'Moduli / Dozvole', 'app_label': 'modules',
                            'app_url': modules[0]['admin_url'], 'has_module_perms': True, 'models': modules})
        return [app for app in apps if app['models']]

    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_superuser
