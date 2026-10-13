from unittest.mock import Mock, patch
from django.test import TestCase, RequestFactory
from django.template.loader import render_to_string
from django.contrib.auth.models import AnonymousUser
from .utils.images import banner_image_responsive_meta


class HomeHeroLoadingTests(TestCase):
    def test_remote_original_does_not_advertise_unmeasured_width(self):
        field = Mock()
        field.name = 'banners/mobile/original.jpg'
        field.url = 'https://media.example.com/banners/mobile/original.jpg'
        with patch('EcommerceApp.utils.images._is_remote_storage', return_value=True):
            meta = banner_image_responsive_meta(field, tip='hero_mobile')
        self.assertEqual(meta['src'], field.url)
        self.assertEqual(meta['preload_src'], field.url)
        self.assertEqual(meta['srcset'], '')
        field.storage.exists.assert_not_called()
        field.open.assert_not_called()

    def test_preload_precedes_styles_and_matches_picture_sources(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        request.session = {}
        slide = {'image': 'https://media.example.com/desktop.webp', 'image_mobile': 'https://media.example.com/mobile.jpg', 'has_mobile_image': True, 'image_width':2172,'image_height':724,'image_srcset':'','image_mobile_srcset':''}
        html = render_to_string('bera_reference/home.html', {'request':request,'user':request.user,'hero_slides':[slide], 'lcp_image_url':slide['image'],'lcp_image_sizes':'100vw','filters_active':False}, request=request)
        self.assertLess(html.index('name="viewport"'), html.index('rel="preload" as="image"'))
        self.assertLess(html.index('rel="preload" as="image"'), html.index('rel="stylesheet"'))
        self.assertIn('href="https://media.example.com/mobile.jpg" imagesizes="(min-width: 1620px) 1580px, (min-width: 1600px) calc(100vw - 40px), (min-width: 1220px) 1180px, calc(100vw - 40px)" media="(max-width: 768px)" fetchpriority="high"', html)
        self.assertIn('media="(min-width: 768.01px)" fetchpriority="high"', html)
        self.assertIn('srcset="https://media.example.com/mobile.jpg"', html)
        self.assertIn('loading="eager" fetchpriority="high"', html)
