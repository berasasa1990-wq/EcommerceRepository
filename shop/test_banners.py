from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import BannerForm
from .models import Banner
from .test_image_uploads import upload


class HeroBannerTests(TestCase):
    def test_uploads_and_editable_text_render_on_home(self):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            form = BannerForm({
                'position': 'hero', 'title': 'Nova ponuda', 'eyebrow': 'Noviteti',
                'subtitle': 'Opis ponude', 'button_text': 'Pogledaj artikle',
                'url': '/noviteti/', 'is_active': 'on',
            }, {'image': upload(), 'secondary_image': upload()}, instance=Banner.objects.get(position='hero'))
            self.assertTrue(form.is_valid(), form.errors)
            banner = form.save()
            for image in (banner.image, banner.secondary_image):
                self.assertTrue(image.name.endswith('.avif'))
                self.assertLessEqual(image.size, 80_000)
            response = self.client.get(reverse('home'))
            for text in ('Nova ponuda', 'bera-uploaded-hero', banner.image.url):
                self.assertContains(response, text)
            self.assertNotContains(response, 'Vaš svijet.')
            banner.is_active = False
            banner.save()
            self.assertNotContains(self.client.get(reverse('home')), 'bera-hero-inner')

    def test_banner_keeps_resolution_and_uses_80kb_budget(self):
        from PIL import Image
        artwork = Image.effect_noise((800, 300), 40).convert('RGB')
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            form = BannerForm({'position': 'middle', 'title': 'Veliki banner', 'is_active': 'on'}, {'image': upload(artwork)})
            self.assertTrue(form.is_valid(), form.errors)
            banner = form.save()
            self.assertLessEqual(banner.image.size, 80_000)
            self.assertGreater(banner.image.size, 15_000)
            with Image.open(banner.image) as image:
                self.assertEqual(image.size, artwork.size)
            self.assertContains(self.client.get(reverse('home')), banner.image.url)

    def test_text_only_hero(self):
        form = BannerForm({'position': 'hero', 'title': 'Tekst bez slike', 'is_active': 'on'}, instance=Banner.objects.get(position='hero'))
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertContains(self.client.get(reverse('home')), 'Tekst bez slike')

    def test_offer_still_requires_image(self):
        form = BannerForm({'position': 'offer_left', 'title': 'Ponuda'})
        self.assertFalse(form.is_valid())
        self.assertIn('image', form.errors)

    def test_existing_written_banner_is_available_to_edit(self):
        banner = Banner.objects.get(position='hero')
        self.assertEqual(banner.title, 'Vaš svijet.\nUz Apple.')
        self.assertEqual(banner.feature_items, ['iPhone', 'MacBook', 'AirPods', 'Apple Watch'])
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Vaš svijet.<br>Uz Apple.')

    def test_inactive_placeholder_becomes_editable_written_hero(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        Banner.objects.filter(position='hero').update(
            title='Glavni banner', is_active=False, eyebrow='', subtitle='',
            image='banners/existing.avif',
        )
        migration = import_module('shop.migrations.0021_connect_written_hero')
        migration.connect_written_hero(apps, connection.schema_editor())
        banner = Banner.objects.get(position='hero')
        self.assertTrue(banner.is_active)
        self.assertEqual(banner.title, 'Vaš svijet.\nUz Apple.')
        self.assertEqual(banner.image.name, 'banners/existing.avif')

    def test_delete_requires_staff_and_post_and_removes_home_banner(self):
        from django.contrib.auth import get_user_model
        banner = Banner.objects.get(position='hero')
        url = reverse('panel_banner_delete', args=[banner.pk])
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(Banner.objects.filter(pk=banner.pk).exists())
        staff = get_user_model().objects.create_user('banner-manager', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(Banner.objects.filter(pk=banner.pk).exists())
        self.assertRedirects(self.client.post(url), reverse('panel_section', args=['banneri']))
        self.assertFalse(Banner.objects.filter(pk=banner.pk).exists())
        self.assertNotContains(self.client.get(reverse('home')), 'bera-hero-inner')

    def test_add_another_hero_without_replacing_existing_banner(self):
        original = Banner.objects.get(position='hero')
        form = BannerForm({
            'position': 'hero', 'title': 'Drugi glavni banner',
            'subtitle': 'Drugi opis', 'button_text': 'Otvori', 'is_active': 'on',
        })
        self.assertTrue(form.is_valid(), form.errors)
        new_banner = form.save()
        response = self.client.get(reverse('home'))
        self.assertContains(response, original.title.replace('\n', '<br>'))
        self.assertContains(response, new_banner.title)
        self.assertContains(response, f'id="customHeroTitle{original.pk}"')
        self.assertContains(response, f'id="customHeroTitle{new_banner.pk}"')
        self.assertEqual(Banner.objects.filter(position='hero').count(), 2)
