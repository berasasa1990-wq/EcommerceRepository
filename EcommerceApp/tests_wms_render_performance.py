from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase

from .context_processors import _is_light_request_path, nav_categories


class WMSRenderPerformanceTests(SimpleTestCase):
    def test_workspace_does_not_prepare_storefront_queries_or_cart(self):
        for path in ('/wms', '/wms/', '/wms/zalihe/', '/wms/narudzbe/1/odvajanje/'):
            with self.subTest(path=path), patch(
                'EcommerceApp.context_processors.SiteSettings.load',
                return_value=SimpleNamespace(),
            ), patch('EcommerceApp.context_processors._build_nav_categories') as menu, patch(
                'EcommerceApp.context_processors.Cart'
            ) as cart, patch('EcommerceApp.context_processors._cached_popup_akcije') as offers:
                context = nav_categories(RequestFactory().get(path))
                self.assertTrue(context['is_wms_workspace'])
                menu.assert_not_called()
                cart.assert_not_called()
                offers.assert_not_called()

    def test_public_shop_and_panel_keep_their_storefront_context(self):
        for path in ('/', '/artikal/test/', '/korpa/', '/panel', '/panel/sekcija/product/'):
            self.assertFalse(_is_light_request_path(path))
