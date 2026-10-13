import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, RequestFactory

from .bera_home import homepage_product_preload
from .models import SiteSettings


class HomeCriticalDeliveryTests(SimpleTestCase):
    def test_css_split_keeps_every_rule_in_the_original_order(self):
        root = Path(settings.BASE_DIR) / 'EcommerceApp'
        template = (root / 'template/bera_reference/partials/home_critical_css.html').read_text()
        prefix = template.split('<style id="home-critical-css">', 1)[1].split('</style>', 1)[0]
        prefix = re.sub(r"\{% static 'bera_reference/(fonts/[^']+)' %\}", r'../\1', prefix)
        css = root / 'static/bera_reference/css'
        self.assertEqual(prefix + (css / 'home-core-rest.bundle.css').read_text(),
                         (css / 'home-core.bundle.css').read_text())
        self.assertIn('@font-face', prefix)
        self.assertIn('.header{', prefix)
        self.assertNotIn('@font-face', (css / 'home-core-rest.bundle.css').read_text())

    def product(self, name):
        return SimpleNamespace(prikazna_slika=SimpleNamespace(url='/media/' + name),
                               prikazna_slika_responsive={'src': '/media/' + name,
                                  'srcset': '/media/small-' + name + ' 320w, /media/' + name + ' 600w'})

    def test_preload_matches_featured_then_final_curated_products(self):
        request = RequestFactory().get('/')
        featured, curated = self.product('featured.avif'), self.product('curated.avif')
        for sections, expected in [
            ({'featured_products': [featured], 'latest_products': [curated]}, 'featured.avif'),
            ({'latest_products': [curated]}, 'curated.avif'),
            ({'home_new_offer_products': [curated]}, 'curated.avif'),
        ]:
            with self.subTest(expected=expected, sections=sections.keys()):
                result = homepage_product_preload(sections, request)
                self.assertTrue(result['lcp_image_url'].endswith('/' + expected))
                self.assertIn(expected, result['lcp_image_srcset'])
                self.assertEqual(result['lcp_image_sizes'], '(max-width: 768px) 50vw, 20vw')

    def test_hero_search_and_empty_sections_never_preload_stale_products(self):
        request = RequestFactory().get('/')
        self.assertEqual(homepage_product_preload({'hero_slides': [{}]}, request), {})
        self.assertEqual(homepage_product_preload({'filters_active': True}, request), {})
        self.assertIsNone(homepage_product_preload({}, request)['lcp_image_url'])


class HomeDeliveryRenderingTests(TestCase):
    def test_first_screen_css_and_font_urls_render_without_duplicate_core(self):
        html = render_to_string('bera_reference/home.html', {
            'home_css_optimized': True, 'site_settings': SiteSettings(),
        })
        self.assertIn('id="home-critical-css"', html)
        self.assertIn('/static/bera_reference/fonts/inter-latin-700.woff2', html)
        self.assertIn('/css/home-core-rest.bundle.css', html)
        self.assertNotIn('/css/home-core.bundle.css', html)

    def test_locked_scratch_module_has_neither_popup_nor_stylesheet(self):
        with patch('EcommerceApp.bera_panel_modules.module_locked', return_value=True):
            html = render_to_string('bera_reference/home.html', {'site_settings': SiteSettings()})
        self.assertNotIn('/css/scratch-game.css', html)
        self.assertNotIn('id="scratchGame"', html)
