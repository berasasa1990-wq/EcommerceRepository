from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from .models import Brand, Product, SiteSettings, B2BLive, WarehouseMovement
from .panel_admin import panel_admin_site


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class SettingsWorkspaceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = get_user_model().objects.create_user('workspace-staff', password='fixture-password', is_staff=True)
        self.owner = get_user_model().objects.create_superuser('workspace-owner', 'owner@example.invalid', 'fixture-password')
        self.client.force_login(self.staff)

    def test_sidebar_has_sections_add_links_and_selection(self):
        settings = self.client.get(reverse('staff_settings'))
        self.assertEqual([item['key'] for item in settings.context['settings_navigation']], ['site', 'banner', 'userprofile', 'pageseo'])
        for key in ('banner', 'userprofile', 'pageseo'):
            selected = self.client.get(reverse('staff_settings'), {'sekcija': key})
            self.assertEqual(selected.context['selected_settings']['key'], key)
            self.assertContains(selected, reverse('panel_admin:EcommerceApp_' + key + '_add'))
        panel = self.client.get(reverse('staff_panel'))
        self.assertFalse({'banner', 'userprofile', 'pageseo'} & {item['key'] for item in panel.context['model_sections']})
        for item in panel.context['model_sections']:
            self.assertContains(panel, item['workspace_url'])
            page = self.client.get(item['workspace_url'])
            self.assertEqual(page.status_code, 200)
            if item['key'] != 'scratchprize':
                self.assertEqual(page.context['selected_settings']['key'], item['key'])
        response = self.client.get(reverse('staff_model_workspace', args=['product']))
        self.assertEqual(response.context['selected_settings']['url'], reverse('panel_admin:EcommerceApp_product_changelist'))
        self.assertContains(response, reverse('panel_admin:EcommerceApp_product_add'))
        self.assertNotContains(response, 'href="/admin/')
        promotions = self.client.get(reverse('staff_model_workspace', args=['akcija']), {'sekcija': 'upselloffer'})
        self.assertEqual([item['key'] for item in promotions.context['settings_navigation']], ['akcija', 'upselloffer'])
        self.assertEqual(promotions.context['selected_settings']['key'], 'upselloffer')
        self.assertContains(promotions, reverse('panel_admin:EcommerceApp_upselloffer_add'))
        self.assertNotIn('upselloffer', [item['key'] for item in panel.context['model_sections']])

    def test_products_workspace_groups_tags_categories_and_brands(self):
        from .panel_settings import PRODUCT_KEYS
        panel = self.client.get(reverse('staff_panel'))
        keys = {item['key'] for item in panel.context['model_sections']}
        self.assertIn('product', keys)
        self.assertFalse(keys & set(PRODUCT_KEYS[1:]))
        url = reverse('staff_model_workspace', args=['product'])
        for key in PRODUCT_KEYS:
            page = self.client.get(url, {'sekcija': key})
            self.assertEqual(page.status_code, 200)
            self.assertEqual([item['key'] for item in page.context['settings_navigation']], list(PRODUCT_KEYS))
            self.assertEqual(page.context['selected_settings']['key'], key)
            self.assertEqual(page.context['workspace_title'], 'Artikli')
            self.assertContains(page, reverse('panel_admin:EcommerceApp_' + key + '_add'))

    def test_profiles_hide_superusers_and_reject_their_ids(self):
        from .models import UserProfile
        owner_profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        customer = get_user_model().objects.create_user('visible-profile-customer')
        customer_profile, _ = UserProfile.objects.get_or_create(user=customer)
        for user in (self.staff, self.owner):
            self.client.force_login(user)
            page = self.client.get(reverse('panel_admin:EcommerceApp_userprofile_changelist'))
            ids = {profile.pk for profile in page.context['cl'].queryset}
            self.assertNotIn(owner_profile.pk, ids)
            self.assertIn(customer_profile.pk, ids)
            form_page = self.client.get(reverse('panel_admin:EcommerceApp_userprofile_add'))
            choices = form_page.context['adminform'].form.fields['user'].queryset
            self.assertFalse(choices.filter(pk=self.owner.pk).exists())
            lookup = self.client.get(reverse('panel_admin:autocomplete'), {
                'app_label': 'EcommerceApp', 'model_name': 'userprofile', 'field_name': 'user', 'term': '',
            })
            self.assertEqual(lookup.status_code, 200)
            self.assertNotIn(str(self.owner.pk), {row['id'] for row in lookup.json()['results']})
            changed = self.client.post(reverse('panel_admin:EcommerceApp_userprofile_change', args=[owner_profile.pk]), {
                'user': self.owner.pk, 'telefon': 'unauthorized', '_save': 'Save',
            })
            self.assertEqual(changed.status_code, 302)
        owner_profile.refresh_from_db()
        self.assertNotEqual(owner_profile.telefon, 'unauthorized')

    def test_scratch_group_moves_analytics_and_preserves_permissions(self):
        group = reverse('staff_scratch_workspace')
        analytics = reverse('staff_scratch_analytics')
        self.assertContains(self.client.get(group), reverse('staff_model_workspace', args=['scratchprize']))
        self.assertNotContains(self.client.get(group), 'href="' + analytics + '"')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(group), analytics)
        panel = self.client.get(reverse('staff_panel'))
        self.assertContains(panel, group)
        self.assertNotContains(panel, 'href="' + analytics + '"')
        self.client.logout()
        self.assertEqual(self.client.get(group).status_code, 302)

    def test_b2b_sections_move_into_their_own_panel_workspace(self):
        url = reverse('staff_b2b_workspace')
        self.assertContains(self.client.get(reverse('staff_panel')), url)
        settings = self.client.get(reverse('staff_settings'))
        keys = {item['key'] for item in settings.context['settings_navigation']}
        b2b_keys = {'b2bsettings', 'b2baccount', 'b2bsubmission', 'b2blive'}
        self.assertFalse(keys & b2b_keys)
        response = self.client.get(url, {'sekcija': 'b2baccount'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item['key'] for item in response.context['settings_navigation']}, b2b_keys)
        self.assertEqual(response.context['selected_settings']['key'], 'b2baccount')
        self.assertEqual(response.context['workspace_url'], url)
        for text in ('B2B korisnici', 'B2B narudžbe', 'B2B Live'):
            self.assertContains(response, text)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        customer = get_user_model().objects.create_user('b2b-workspace-customer')
        self.client.force_login(customer)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_all_visible_lists_and_add_forms_render_for_staff(self):
        for app in panel_admin_site.get_app_list(self.client.get(reverse('staff_settings')).wsgi_request):
            for model in app['models']:
                for key in ('admin_url', 'add_url'):
                    if model.get(key):
                        with self.subTest(model=model['object_name'], action=key):
                            response = self.client.get(model[key])
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')

    def test_loyalty_group_contains_system_and_cards(self):
        group = reverse('staff_loyalty_workspace')
        panel = self.client.get(reverse('staff_panel'))
        self.assertContains(panel, group)
        self.assertNotContains(panel, 'href="' + reverse('staff_loyalty_system') + '"')
        self.assertNotIn('loyaltycard', [item['key'] for item in panel.context['model_sections']])
        page = self.client.get(group)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse('staff_loyalty_system'))
        self.assertContains(page, reverse('staff_model_workspace', args=['loyaltycard']))
        self.client.logout()
        self.assertEqual(self.client.get(group).status_code, 302)
        customer = get_user_model().objects.create_user('loyalty-group-customer')
        self.client.force_login(customer)
        self.assertEqual(self.client.get(group).status_code, 302)

    def test_wms_group_contains_warehouse_and_four_model_sections(self):
        from .panel_settings import WMS_KEYS
        group = reverse('staff_wms_workspace')
        panel = self.client.get(reverse('staff_panel'))
        self.assertContains(panel, group)
        self.assertNotContains(panel, 'href="' + reverse('staff_magacin') + '"')
        self.assertFalse(set(WMS_KEYS) & {item['key'] for item in panel.context['model_sections']})
        page = self.client.get(group)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse('staff_magacin'))
        self.assertEqual([item['key'] for item in page.context['wms_sections']], list(WMS_KEYS))
        for key in WMS_KEYS:
            self.assertContains(page, reverse('staff_model_workspace', args=[key]))
        self.client.logout()
        self.assertEqual(self.client.get(group).status_code, 302)
        customer = get_user_model().objects.create_user('wms-group-customer')
        self.client.force_login(customer)
        self.assertEqual(self.client.get(group).status_code, 302)

    def test_existing_editor_and_site_form_are_frameable(self):
        product = Product.objects.create(naziv='Postojeći artikal', sifra='WORKSPACE', cijena=10)
        response = self.client.get(reverse('panel_admin:EcommerceApp_product_change', args=[product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Postojeći artikal')
        self.assertNotContains(response, 'href="/admin/EcommerceApp/')
        self.assertEqual(response.headers['X-Frame-Options'], 'SAMEORIGIN')
        self.assertEqual(self.client.get(reverse('staff_site_settings')).headers['X-Frame-Options'], 'SAMEORIGIN')

    def test_staff_saves_brand_and_redirect_stays_in_workspace(self):
        url = reverse('panel_admin:EcommerceApp_brand_add')
        form = self.client.get(url).context['adminform'].form
        data = {}
        for bound in form:
            if isinstance(bound.field, forms.FileField):
                continue
            value = bound.value()
            if isinstance(bound.field, forms.BooleanField):
                if value:
                    data[bound.html_name] = 'on'
            else:
                data[bound.html_name] = '' if value is None else value
        data.update(naziv='Novi workspace brend', slug='workspace-brend', _save='Save')
        saved = self.client.post(url, data)
        self.assertEqual(saved.status_code, 302)
        self.assertTrue(saved.url.startswith('/panel/podesavanja/sekcije/'))
        self.assertTrue(Brand.objects.filter(naziv='Novi workspace brend').exists())

    def test_django_admin_contains_no_moved_shop_models(self):
        self.assertTrue(all(model._meta.app_label != 'EcommerceApp' for model in admin.site._registry))
        self.assertNotIn(SiteSettings, admin.site._registry)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.client.force_login(self.owner)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '/admin/EcommerceApp/')

    def test_customer_cannot_open_direct_lists_or_submit_model_form(self):
        customer = get_user_model().objects.create_user('workspace-customer')
        self.client.force_login(customer)
        url = reverse('panel_admin:EcommerceApp_brand_add')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.post(url, {'naziv': 'Unauthorized'}).status_code, 302)
        self.assertFalse(Brand.objects.filter(naziv='Unauthorized').exists())

    def test_existing_readonly_and_superuser_restrictions_remain(self):
        request = self.client.get(reverse('staff_settings')).wsgi_request
        self.assertFalse(panel_admin_site._registry[B2BLive].has_add_permission(request))
        self.assertFalse(panel_admin_site._registry[B2BLive].has_change_permission(request))
        self.assertFalse(panel_admin_site._registry[WarehouseMovement].has_delete_permission(request))
        self.assertFalse(panel_admin_site._registry[get_user_model()].has_change_permission(request))
        url = reverse('panel_admin:auth_user_change', args=[self.owner.pk])
        self.assertEqual(self.client.post(url, {'is_superuser': 'on'}).status_code, 403)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_superuser)

    def test_admin_model_posts_keep_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        response = client.post(reverse('panel_admin:EcommerceApp_brand_add'), {'naziv': 'No CSRF'})
        self.assertEqual(response.status_code, 403)
