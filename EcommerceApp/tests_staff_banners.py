from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from .models import Banner, SiteSettings
from .views import staff_banners


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class BannerManagerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('banners-admin', 'banners@example.com', 'password')

    def request(self, query='', data=None):
        factory = RequestFactory()
        request = factory.get('/nalog/admin/banneri/' + query) if data is None else factory.post('/nalog/admin/banneri/' + query, data)
        request.user = self.admin
        request.session = {}
        return request

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_list_has_add_and_edit_actions(self, context):
        response = staff_banners(self.request())
        self.assertContains(response, '?add=1')
        self.assertContains(response, '?edit=hero')
        self.assertContains(response, '?edit=bestsellers')

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_add_opens_form_and_rejects_missing_media(self, context):
        response = staff_banners(self.request('?add=1', {'tip': 'featured', 'redoslijed': '0', 'aktivan': 'on'}))
        self.assertContains(response, 'Banner mora imati sliku ili video.')

    @patch('EcommerceApp.views.messages.success')
    def test_edit_main_preserves_other_banner(self, success):
        site = SiteSettings.load()
        SiteSettings.objects.filter(pk=site.pk).update(bestsellers_banner_link='/sacuvano/')
        response = staff_banners(self.request('?edit=hero', {'hero_link': 'https://example.com/novo/'}))
        self.assertEqual(response.status_code, 302)
        site.refresh_from_db()
        self.assertEqual(site.hero_link, 'https://example.com/novo/')
        self.assertEqual(site.bestsellers_banner_link, '/sacuvano/')

    def test_staff_cannot_edit(self):
        request = self.request('?add=1')
        request.user.is_superuser = False
        self.assertEqual(staff_banners(request).status_code, 302)

    @patch('EcommerceApp.views.messages.success')
    def test_delete_banner(self, success):
        banner = Banner.objects.create(naslov='Za brisanje', slika='banners/test.jpg')
        response = staff_banners(self.request(data={'delete_banner': str(banner.pk)}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Banner.objects.filter(pk=banner.pk).exists())

    @patch('EcommerceApp.views.messages.success')
    def test_delete_fixed_banner_preserves_other_banner(self, success):
        site = SiteSettings.load()
        for target, image, link, other in (
            ('hero', 'hero_slika', 'hero_link', 'bestsellers_banner_slika'),
            ('bestsellers', 'bestsellers_banner_slika', 'bestsellers_banner_link', 'hero_slika'),
        ):
            with self.subTest(target=target):
                SiteSettings.objects.filter(pk=site.pk).update(**{image: 'banners/test.jpg', link: '/test/', other: 'banners/keep.jpg'})
                response = staff_banners(self.request(data={'delete_banner': target}))
                self.assertEqual(response.status_code, 302)
                site.refresh_from_db()
                self.assertFalse(getattr(site, image))
                self.assertEqual(getattr(site, link), '')
                self.assertEqual(getattr(site, other).name, 'banners/keep.jpg')

    def test_staff_cannot_delete(self):
        banner = Banner.objects.create(naslov='Sačuvaj')
        request = self.request(data={'delete_banner': str(banner.pk)})
        request.user.is_superuser = False
        self.assertEqual(staff_banners(request).status_code, 302)
        self.assertTrue(Banner.objects.filter(pk=banner.pk).exists())

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_get_cannot_delete(self, context):
        banner = Banner.objects.create(naslov='Sačuvaj')
        response = staff_banners(self.request('?delete_banner=' + str(banner.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Banner.objects.filter(pk=banner.pk).exists())

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_promo_editors_have_upload_fields(self, context):
        for key in ('promo_new', 'promo_sale'):
            with self.subTest(key=key):
                response = staff_banners(self.request('?edit=' + key))
                self.assertContains(response, 'name="' + key + '_slika"')
                self.assertContains(response, 'type="file"')

    @patch('EcommerceApp.views.messages.success')
    def test_promo_save_preserves_other_banner(self, success):
        site = SiteSettings.load()
        SiteSettings.objects.filter(pk=site.pk).update(promo_sale_link='/?akcija=1')
        response = staff_banners(self.request('?edit=promo_new', {'promo_new_link': '/?noviteti=1'}))
        self.assertEqual(response.status_code, 302)
        site.refresh_from_db()
        self.assertEqual(site.promo_new_href, '/?noviteti=1')
        self.assertEqual(site.promo_sale_link, '/?akcija=1')

    def test_promo_images_replace_text_content(self):
        from django.template.loader import render_to_string
        site = SiteSettings(promo_new_slika='site/promos/new.jpg', promo_sale_slika='site/promos/sale.jpg')
        html = render_to_string('partials/home_reference_promos.html', {'site_settings': site})
        self.assertIn('site/promos/new.jpg', html)
        self.assertIn('site/promos/sale.jpg', html)
        self.assertNotIn('<article', html)
        self.assertIn('/?noviteti=1', html)
        self.assertIn('/?akcija=1', html)

    def test_registration_image_replaces_copy(self):
        from django.template.loader import render_to_string
        site = SiteSettings(registration_slika='site/registration/banner.jpg')
        html = render_to_string('partials/home_registration_banner.html', {'site_settings': site})
        self.assertIn('img/registration-banner-1600x320.png', html)
        self.assertNotIn('site/registration/banner.jpg', html)
        self.assertIn('/registracija/?loyalty_popup=1', html)
        self.assertNotIn('<p>', html)
        self.assertIn('home-registration-banner--uploaded', html)

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_registration_banner_is_fixed(self, context):
        from django.http import Http404
        response = staff_banners(self.request())
        self.assertNotContains(response, '?edit=registration')
        with self.assertRaises(Http404):
            staff_banners(self.request('?edit=registration'))
        with self.assertRaises(Http404):
            staff_banners(self.request(data={'delete_banner': 'registration'}))
