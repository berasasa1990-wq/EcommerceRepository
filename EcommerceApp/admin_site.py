"""BERA module navigation with existing model permissions preserved."""
from django.contrib.admin import AdminSite


class BeraAdminSite(AdminSite):
    def get_app_list(self, request, app_label=None):
        apps = super().get_app_list(request, app_label)
        modules = []
        for app in apps:
            modules.extend(row for row in app['models'] if row['object_name'] == 'ModulePermissions')
            # These editors are now listed in panel settings. Keep their
            # registrations for existing URLs, actions and autocomplete fields.
            app['models'] = [] if app['app_label'] in {'EcommerceApp', 'auth'} else [
                row for row in app['models'] if row['object_name'] != 'ModulePermissions'
            ]
        if modules:
            apps.insert(0, {'name': 'Moduli / Dozvole', 'app_label': 'modules',
                            'app_url': modules[0]['admin_url'], 'has_module_perms': True, 'models': modules})
        return [app for app in apps if app['models']]
