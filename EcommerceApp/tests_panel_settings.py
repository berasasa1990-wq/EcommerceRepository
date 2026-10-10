from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse, NoReverseMatch

from .models import SiteSettings, HomeTrustItem, HomeFeaturedProduct, Product


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class PanelSettingsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.site = SiteSettings.load()
        self.site.company_name = 'Postojeća firma'
        self.site.kontakt_telefon = '+38760000111'
        self.site.save()
        self.staff = get_user_model().objects.create_user('settings-staff', password='fixture-password', is_staff=True)
        self.trust = HomeTrustItem.objects.create(postavke=self.site, naslov='Postojeća dostava')
        self.product = Product.objects.create(naziv='Demo artikal', sifra='SETTINGS-DEMO', cijena=10, aktivan=True)
        HomeFeaturedProduct.objects.create(postavke=self.site, artikal=self.product)

    def payload(self, response):
        data = {}
        def add_form(form):
            for bound in form:
                if isinstance(bound.field, forms.FileField):
                    continue
                value = bound.value()
                if isinstance(bound.field, forms.BooleanField):
                    if value:
                        data[bound.html_name] = 'on'
                elif isinstance(bound.field, forms.ModelMultipleChoiceField):
                    data[bound.html_name] = value or []
                else:
                    data[bound.html_name] = '' if value is None else str(value)
        add_form(response.context['settings_form'])
        for group in response.context['inline_groups']:
            add_form(group['formset'].management_form)
            for form in group['formset']:
                add_form(form)
        return data

    def login(self):
        self.client.force_login(self.staff)

    def test_permissions_and_removed_admin_registration(self):
        self.assertEqual(self.client.get(reverse('staff_site_settings')).status_code, 302)
        customer = get_user_model().objects.create_user('settings-customer')
        self.client.force_login(customer)
        self.assertEqual(self.client.get(reverse('staff_site_settings')).status_code, 302)
        self.assertEqual(self.client.post(reverse('staff_site_settings'), {}).status_code, 302)
        self.login()
        self.assertEqual(self.client.get(reverse('staff_site_settings')).status_code, 200)
        self.assertNotIn(SiteSettings, admin.site._registry)
        with self.assertRaises(NoReverseMatch):
            reverse('admin:EcommerceApp_sitesettings_change', args=[self.site.pk])
        self.assertContains(self.client.get('/panel'), reverse('staff_settings'))

    def test_existing_settings_and_all_six_inline_groups_render(self):
        self.login()
        response = self.client.get(reverse('staff_site_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Postojeća firma')
        self.assertContains(response, 'Postojeća dostava')
        self.assertContains(response, 'Demo artikal')
        self.assertEqual(len(response.context['inline_groups']), 6)
        for name in ('logo', 'favicon', 'newsletter_banner', 'kontakt_telefon', 'seo_title', 'dostava_cijena'):
            self.assertIn(name, response.context['settings_form'].fields)

    def test_save_preserves_related_content_and_invalidates_cache(self):
        self.login()
        data = self.payload(self.client.get(reverse('staff_site_settings')))
        data.update(company_name='Nova firma', kontakt_telefon='+38760000222', boja_menija='black')
        cache.set('seo_org_json_ld_webshop_v1', 'old')
        response = self.client.post(reverse('staff_site_settings'), data)
        self.assertRedirects(response, reverse('staff_site_settings'))
        self.site.refresh_from_db()
        self.assertEqual(self.site.company_name, 'Nova firma')
        self.assertEqual(SiteSettings.load().boja_menija, 'black')
        self.assertEqual(HomeTrustItem.objects.get(pk=self.trust.pk).naslov, 'Postojeća dostava')
        self.assertEqual(HomeFeaturedProduct.objects.get(postavke=self.site).artikal_id, self.product.pk)
        self.assertNotEqual(cache.get('seo_org_json_ld_webshop_v1'), 'old')

    def test_add_edit_and_delete_inline_rows(self):
        self.login()
        response = self.client.get(reverse('staff_site_settings'))
        data = self.payload(response)
        group = response.context['inline_groups'][0]
        prefix = group['formset'].prefix
        data[prefix + '-TOTAL_FORMS'] = '2'
        data[prefix + '-0-DELETE'] = 'on'
        data.update({prefix + '-1-naslov': 'Nova pogodnost', prefix + '-1-podnaslov': '',
                     prefix + '-1-ikona': 'check', prefix + '-1-redoslijed': '1',
                     prefix + '-1-aktivan': 'on'})
        self.assertRedirects(self.client.post(reverse('staff_site_settings'), data), reverse('staff_site_settings'))
        self.assertFalse(HomeTrustItem.objects.filter(postavke=self.site, naslov='Postojeća dostava').exists())
        self.assertTrue(HomeTrustItem.objects.filter(postavke=self.site, naslov='Nova pogodnost').exists())

    def test_invalid_inline_does_not_partially_save_settings(self):
        self.login()
        response = self.client.get(reverse('staff_site_settings'))
        data = self.payload(response)
        data['company_name'] = 'Ne smije se sačuvati'
        prefix = response.context['inline_groups'][0]['formset'].prefix
        data[prefix + '-0-naslov'] = ''
        saved = self.client.post(reverse('staff_site_settings'), data)
        self.assertEqual(saved.status_code, 200)
        self.site.refresh_from_db()
        self.assertEqual(self.site.company_name, 'Postojeća firma')
        self.assertEqual(HomeTrustItem.objects.get(pk=self.trust.pk).naslov, 'Postojeća dostava')
        self.assertContains(saved, 'Podešavanja nisu sačuvana.')
        self.assertContains(saved, 'red 1')

    def test_save_button_json_request_persists_full_form(self):
        self.login()
        page = self.client.get(reverse('staff_site_settings'))
        self.assertContains(page, 'novalidate')
        data = self.payload(page)
        data['company_name'] = 'Sačuvana firma'
        data['kontakt_telefon'] = '+38760123456'
        response = self.client.post(reverse('staff_site_settings'), data, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], reverse('staff_site_settings'))
        self.site.refresh_from_db()
        self.assertEqual(self.site.company_name, 'Sačuvana firma')
        self.assertEqual(self.site.kontakt_telefon, '+38760123456')
        self.assertTrue(HomeTrustItem.objects.filter(pk=self.trust.pk).exists())

    def test_json_save_reports_inline_errors_without_partial_save(self):
        self.login()
        page = self.client.get(reverse('staff_site_settings'))
        data = self.payload(page)
        data['company_name'] = 'Ne smije se sačuvati'
        prefix = page.context['inline_groups'][0]['formset'].prefix
        data[prefix + '-0-naslov'] = ''
        response = self.client.post(reverse('staff_site_settings'), data, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(any('red 1' in error for error in response.json()['errors']))
        self.site.refresh_from_db()
        self.assertEqual(self.site.company_name, 'Postojeća firma')

    def test_missing_formsets_do_not_delete_existing_content(self):
        self.login()
        response = self.client.get(reverse('staff_site_settings'))
        data = self.payload(response)
        prefix = response.context['inline_groups'][0]['formset'].prefix
        for key in list(data):
            if key.startswith(prefix + '-'):
                data.pop(key)
        data['company_name'] = 'Sačuvano bez izostavljene tabele'
        self.assertRedirects(self.client.post(reverse('staff_site_settings'), data), reverse('staff_site_settings'))
        self.site.refresh_from_db()
        self.assertEqual(self.site.company_name, 'Sačuvano bez izostavljene tabele')
        self.assertTrue(HomeTrustItem.objects.filter(pk=self.trust.pk).exists())

    def test_all_omitted_formsets_preserve_rows_and_save_settings(self):
        self.login()
        page = self.client.get(reverse('staff_site_settings'))
        data = self.payload(page)
        prefixes = [group['formset'].prefix + '-' for group in page.context['inline_groups']]
        data = {key: value for key, value in data.items() if not any(key.startswith(prefix) for prefix in prefixes)}
        data['kontakt_telefon'] = '+38760999888'
        response = self.client.post(reverse('staff_site_settings'), data, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.site.refresh_from_db()
        self.assertEqual(self.site.kontakt_telefon, '+38760999888')
        self.assertTrue(HomeTrustItem.objects.filter(pk=self.trust.pk).exists())
        self.assertTrue(HomeFeaturedProduct.objects.filter(postavke=self.site, artikal=self.product).exists())

    def test_partial_formset_without_management_is_rejected(self):
        self.login()
        page = self.client.get(reverse('staff_site_settings'))
        data = self.payload(page)
        prefix = page.context['inline_groups'][0]['formset'].prefix
        data.pop(prefix + '-TOTAL_FORMS')
        data.pop(prefix + '-INITIAL_FORMS')
        data['company_name'] = 'Ne smije se sačuvati'
        response = self.client.post(reverse('staff_site_settings'), data, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 400)
        self.site.refresh_from_db()
        self.assertEqual(self.site.company_name, 'Postojeća firma')
        self.assertTrue(HomeTrustItem.objects.filter(pk=self.trust.pk).exists())

    def test_file_upload_is_saved_with_settings(self):
        from io import BytesIO
        from PIL import Image
        self.login()
        data = self.payload(self.client.get(reverse('staff_site_settings')))
        fixture = BytesIO()
        Image.new('RGB', (2, 2), 'white').save(fixture, format='PNG')
        png = fixture.getvalue()
        data['newsletter_banner'] = SimpleUploadedFile('demo.png', png, content_type='image/png')
        self.assertRedirects(self.client.post(reverse('staff_site_settings'), data), reverse('staff_site_settings'))
        self.site.refresh_from_db()
        self.assertTrue(self.site.newsletter_banner.name)

    def test_uploaded_logo_is_used_on_storefront(self):
        from io import BytesIO
        from PIL import Image
        self.login()
        data = self.payload(self.client.get(reverse('staff_site_settings')))
        fixture = BytesIO()
        Image.new('RGB', (120, 30), 'blue').save(fixture, format='PNG')
        data['logo'] = SimpleUploadedFile('new-logo.png', fixture.getvalue(), content_type='image/png')
        response = self.client.post(reverse('staff_site_settings'), data)
        self.assertRedirects(response, reverse('staff_site_settings'))
        self.site.refresh_from_db()
        self.assertTrue(self.site.logo.name)
        self.assertTrue(self.site.logo.storage.exists(self.site.logo.name))
        self.client.logout()
        self.assertContains(self.client.get(reverse('home')), self.site.logo.url)

    def test_logo_upload_saves_without_other_settings_or_formsets(self):
        from io import BytesIO
        from PIL import Image
        self.login()
        # Prime the settings cache before replacing the logo.
        self.client.get(reverse('home'))
        fixture = BytesIO()
        Image.new('RGBA', (120, 30), (20, 100, 200, 255)).save(fixture, format='PNG')
        response = self.client.post(reverse('staff_settings_logo'), {
            'logo': SimpleUploadedFile('logo.png', fixture.getvalue(), content_type='image/png'),
            'company_name': 'Ne mijenjaj firmu',
        })
        self.assertEqual(response.status_code, 200)
        self.site.refresh_from_db()
        self.assertEqual(self.site.company_name, 'Postojeća firma')
        self.assertEqual(response.json()['url'], self.site.logo.url)
        self.assertContains(self.client.get(reverse('home')), self.site.logo.url)
        self.assertTrue(HomeTrustItem.objects.filter(pk=self.trust.pk).exists())

    def test_logo_upload_rejects_invalid_files_and_nonstaff(self):
        url = reverse('staff_settings_logo')
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        customer = get_user_model().objects.create_user('logo-customer')
        self.client.force_login(customer)
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        self.login()
        self.assertEqual(self.client.post(url, {}).status_code, 400)
        self.assertEqual(self.client.post(url, {
            'logo': SimpleUploadedFile('fake.png', b'not an image', content_type='image/png'),
        }).status_code, 400)
        self.site.refresh_from_db()
        self.assertFalse(self.site.logo)

    def test_lookup_requires_staff_and_searches_existing_products(self):
        url = reverse('staff_settings_lookup')
        self.assertEqual(self.client.get(url, {'model': 'Product', 'q': 'SETTINGS'}).status_code, 302)
        self.login()
        response = self.client.get(url, {'model': 'Product', 'q': 'SETTINGS'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results'][0]['id'], self.product.pk)
        self.assertEqual(self.client.get(url, {'model': 'User'}).status_code, 400)
