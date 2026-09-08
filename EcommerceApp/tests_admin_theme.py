from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Product, SiteSettings, Akcija


class Stylesheets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'link' and values.get('rel') == 'stylesheet':
            self.urls.append(values.get('href', ''))


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class RetroAdminThemeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('theme-admin', 'theme@example.com', 'test')
        self.product = Product.objects.create(naziv='Tema test', cijena='10')
        self.site = SiteSettings.load()
        self.ai = Akcija.objects.create(naziv='AI prodaja / AI dwell', tip=Akcija.Tip.AI_PRODAJA)

    def assert_theme(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        self.assertContains(response, 'admin/css/retro-admin.v20260908.css')
        self.assertContains(response, 'admin/js/retro-admin.v20260908.js')
        parser = Stylesheets()
        parser.feed(response.content.decode())
        self.assertTrue(parser.urls[-1].endswith('retro-admin.v20260908.css'), parser.urls)
        return response

    def test_login_has_shared_theme(self):
        self.assert_theme(reverse('admin:login'))

    def test_dashboard_lists_forms_history_and_delete_share_theme(self):
        self.client.force_login(self.user)
        for url in [reverse('admin:index'), reverse('admin:EcommerceApp_product_changelist'),
                    reverse('admin:EcommerceApp_product_add'),
                    reverse('admin:EcommerceApp_product_change', args=[self.product.pk]),
                    reverse('admin:EcommerceApp_product_history', args=[self.product.pk]),
                    reverse('admin:EcommerceApp_product_delete', args=[self.product.pk]),
                    reverse('admin:password_change')]:
            with self.subTest(url=url):
                self.assert_theme(url)

    def test_settings_and_ai_keep_existing_controls_and_load_theme_last(self):
        self.client.force_login(self.user)
        self.assert_theme(reverse('admin:EcommerceApp_sitesettings_change', args=[self.site.pk]))
        response = self.assert_theme(reverse('admin:EcommerceApp_akcija_change', args=[self.ai.pk]))
        self.assertContains(response, 'ai-settings.v20260909.js')
        self.assertContains(response, 'id_browse_interest_mode')
        self.assertContains(response, 'name="_save"')

    def test_custom_quick_entry_and_import_share_theme(self):
        self.client.force_login(self.user)
        for name in ['admin:EcommerceApp_product_brzi_unos', 'admin:EcommerceApp_product_odoo_import']:
            with self.subTest(name=name):
                self.assert_theme(reverse(name))

    def test_related_object_popup_keeps_popup_mode(self):
        self.client.force_login(self.user)
        response = self.assert_theme(reverse('admin:EcommerceApp_product_add')+'?_popup=1')
        self.assertContains(response, 'name="_popup"')
