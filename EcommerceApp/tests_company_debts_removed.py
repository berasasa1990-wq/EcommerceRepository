from django.core.management import get_commands
from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, resolve
from django.contrib.auth.models import AnonymousUser
from .models import SiteSettings


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class CompanyDebtsRemovedTests(SimpleTestCase):
    def test_removed_page_and_import_commands(self):
        with self.assertRaises(Resolver404):
            resolve('/nalog/admin/dugovanja-firmi/')
        commands = get_commands()
        self.assertNotIn('import_company_payments', commands)
        self.assertNotIn('watch_company_payments', commands)

    def test_admin_panel_has_no_company_debts_link(self):
        html = render_to_string('staff/admin_panel.html', {
            'site_settings': SiteSettings(), 'user': AnonymousUser(),
        })
        self.assertNotIn('Dugovanja Firmi', html)
        self.assertNotIn('dugovanja-firmi', html)
