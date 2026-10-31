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
                self.assertContains(response, 'name="' + key + '_slika_mobilna"')

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

    def test_registration_banner_has_fixed_text_and_benefits(self):
        from django.template.loader import render_to_string
        site = SiteSettings(registration_slika='site/registration/banner.jpg')
        html = render_to_string('partials/home_registration_banner.html', {'site_settings': site})
        self.assertNotIn('site/registration/banner.jpg', html)
        self.assertIn('/registracija/?loyalty_popup=1', html)
        self.assertIn('img/carpologija-nav-logo-dark.jpg', html)
        self.assertNotIn('registration-banner-1600x320.png', html)
        self.assertIn('Registruj se i ostvari 10% popusta', html)
        self.assertIn('Lista želja', html)
        self.assertIn('Pregled narudžbi', html)

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_registration_banner_is_fixed(self, context):
        from django.http import Http404
        response = staff_banners(self.request())
        self.assertNotContains(response, '?edit=registration')
        with self.assertRaises(Http404):
            staff_banners(self.request('?edit=registration'))
        with self.assertRaises(Http404):
            staff_banners(self.request(data={'delete_banner': 'registration'}))

    @patch('EcommerceApp.views.messages.success')
    def test_mobile_promo_upload_preserves_desktop_and_other_promo(self, success):
        import io
        from tempfile import TemporaryDirectory
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        for key in ('promo_new', 'promo_sale'):
            with self.subTest(key=key), TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
                site = SiteSettings.load()
                setattr(site, key + '_slika', 'site/promos/desktop.jpg')
                site.save(update_fields=[key + '_slika'])
                data = io.BytesIO()
                Image.new('RGB', (600, 560), 'white').save(data, format='PNG')
                upload = SimpleUploadedFile('phone.png', data.getvalue(), content_type='image/png')
                response = staff_banners(self.request('?edit=' + key, {key + '_slika_mobilna': upload}))
                self.assertEqual(response.status_code, 302)
                site.refresh_from_db()
                self.assertEqual(getattr(site, key + '_slika').name, 'site/promos/desktop.jpg')
                image = getattr(site, key + '_slika_mobilna')
                self.assertTrue(image)
                with Image.open(image.path) as stored:
                    self.assertEqual(stored.size, (600, 560))

    def test_promo_mobile_picture_source_and_desktop_fallback(self):
        from django.template.loader import render_to_string
        site = SiteSettings(promo_new_slika='promos/desktop.jpg', promo_new_slika_mobilna='promos/phone.jpg',
                            promo_sale_slika='promos/sale.jpg')
        html = render_to_string('partials/home_reference_promos.html', {'site_settings': site})
        self.assertIn(f'<source media="(max-width: 767px)" srcset="{site.promo_new_slika_mobilna.url}">', html)
        self.assertIn(f'src="{site.promo_new_slika.url}"', html)
        self.assertIn(f'src="{site.promo_sale_slika.url}"', html)
        self.assertEqual(html.count('<source'), 1)

    def test_mobile_only_promo_retains_text_for_desktop(self):
        from django.template.loader import render_to_string
        site = SiteSettings(promo_new_slika_mobilna='promos/phone.jpg')
        html = render_to_string('partials/home_reference_promos.html', {'site_settings': site})
        self.assertIn('ref-promo-upload--mobile-only', html)
        self.assertIn('ref-promo--desktop-only', html)
        self.assertIn('OTKRIJTE NOVITETE', html)

    @patch('EcommerceApp.views.messages.success')
    def test_delete_promo_clears_both_images(self, success):
        site = SiteSettings.load()
        SiteSettings.objects.filter(pk=site.pk).update(promo_new_slika='promos/desktop.jpg',
                                                      promo_new_slika_mobilna='promos/phone.jpg',
                                                      promo_sale_slika_mobilna='promos/keep.jpg')
        response = staff_banners(self.request(data={'delete_banner': 'promo_new'}))
        self.assertEqual(response.status_code, 302)
        site.refresh_from_db()
        self.assertFalse(site.promo_new_slika)
        self.assertFalse(site.promo_new_slika_mobilna)
        self.assertEqual(site.promo_sale_slika_mobilna.name, 'promos/keep.jpg')

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_all_fixed_editors_include_phone_image_upload(self, context):
        for key, field in (('hero', 'hero_slika_mobilna'), ('bestsellers', 'bestsellers_banner_slika_mobilna'),
                           ('promo_new', 'promo_new_slika_mobilna'), ('promo_sale', 'promo_sale_slika_mobilna')):
            with self.subTest(key=key):
                response = staff_banners(self.request('?edit=' + key))
                self.assertContains(response, 'name="' + field + '"')
                self.assertContains(response, 'Slika za telefon')

    @patch('EcommerceApp.views.messages.success')
    def test_fixed_phone_upload_preserves_desktop_image(self, success):
        import io
        from tempfile import TemporaryDirectory
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        for key, prefix in (('hero', 'hero'), ('bestsellers', 'bestsellers_banner')):
            with self.subTest(key=key), TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
                site = SiteSettings.load()
                setattr(site, prefix + '_slika', 'site/existing-desktop.jpg')
                site.save(update_fields=[prefix + '_slika'])
                data = io.BytesIO()
                Image.new('RGB', (900, 720), 'white').save(data, format='PNG')
                upload = SimpleUploadedFile('phone.png', data.getvalue(), content_type='image/png')
                response = staff_banners(self.request('?edit=' + key, {prefix + '_slika_mobilna': upload}))
                self.assertEqual(response.status_code, 302)
                site.refresh_from_db()
                self.assertEqual(getattr(site, prefix + '_slika').name, 'site/existing-desktop.jpg')
                self.assertTrue(getattr(site, prefix + '_slika_mobilna'))

    def test_fixed_banners_use_phone_sources(self):
        from django.template.loader import render_to_string
        for template, prefix in (('home_uploaded_banner', 'hero'), ('home_bestsellers_banner', 'bestsellers_banner')):
            with self.subTest(template=template):
                site = SiteSettings(**{prefix + '_slika': 'site/desktop.jpg', prefix + '_slika_mobilna': 'site/phone.jpg'})
                html = render_to_string('partials/' + template + '.html', {'site_settings': site})
                self.assertIn(f'<source media="(max-width: 767px)" srcset="{getattr(site, prefix + "_slika_mobilna").url}">', html)
                self.assertIn('site/desktop.jpg', html)

    @patch('EcommerceApp.views.messages.success')
    def test_delete_main_clears_phone_image(self, success):
        site = SiteSettings.load()
        site.hero_slika_mobilna = 'site/phone.jpg'
        site.save(update_fields=['hero_slika_mobilna'])
        self.assertEqual(staff_banners(self.request(data={'delete_banner': 'hero'})).status_code, 302)
        site.refresh_from_db()
        self.assertFalse(site.hero_slika_mobilna)

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_promo_editor_has_separate_upload_cards_and_no_english_widget(self, context):
        response = staff_banners(self.request('?edit=promo_new'))
        self.assertContains(response, 'Slika za računar')
        self.assertContains(response, 'Upload slike za telefon')
        self.assertContains(response, 'name="promo_new_slika_mobilna"')
        self.assertContains(response, 'data-banner-upload', count=2)
        self.assertContains(response, '600 × 560 px')
        self.assertNotContains(response, 'Currently:')
        self.assertNotContains(response, 'Change:')
        self.assertEqual(response['Cache-Control'], 'no-store, max-age=0')

    @patch('EcommerceApp.views.messages.success')
    def test_clear_phone_image_preserves_desktop(self, success):
        site = SiteSettings.load()
        site.promo_new_slika = 'site/promos/desktop.jpg'
        site.promo_new_slika_mobilna = 'site/promos/phone.jpg'
        site.save(update_fields=['promo_new_slika', 'promo_new_slika_mobilna'])
        response = staff_banners(self.request('?edit=promo_new', {'promo_new_slika_mobilna-clear': 'on'}))
        self.assertEqual(response.status_code, 302)
        site.refresh_from_db()
        self.assertEqual(site.promo_new_slika.name, 'site/promos/desktop.jpg')
        self.assertFalse(site.promo_new_slika_mobilna)

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_first_two_banner_thumbnails_have_real_image_urls(self, context):
        site = SiteSettings.load()
        site.hero_slika = 'site/hero/preview.jpg'
        site.bestsellers_banner_slika = 'site/bestsellers/preview.jpg'
        site.save(update_fields=['hero_slika', 'bestsellers_banner_slika'])
        response = staff_banners(self.request())
        self.assertContains(response, f'src="{site.hero_slika.url}"')
        self.assertContains(response, f'src="{site.bestsellers_banner_slika.url}"')
        self.assertNotContains(response, 'class="banner-manager__thumbnail" src=""')

    @patch('EcommerceApp.views._base_context', return_value={})
    def test_first_two_banner_thumbnails_fall_back_to_mobile_images(self, context):
        site = SiteSettings.load()
        site.hero_slika = ''
        site.bestsellers_banner_slika = ''
        site.hero_slika_mobilna = 'site/mobile/hero.jpg'
        site.bestsellers_banner_slika_mobilna = 'site/mobile/bestsellers.jpg'
        site.save(update_fields=['hero_slika', 'bestsellers_banner_slika', 'hero_slika_mobilna', 'bestsellers_banner_slika_mobilna'])
        response = staff_banners(self.request())
        self.assertContains(response, f'src="{site.hero_slika_mobilna.url}"')
        self.assertContains(response, f'src="{site.bestsellers_banner_slika_mobilna.url}"')
        self.assertNotContains(response, 'class="banner-manager__thumbnail" src=""')
