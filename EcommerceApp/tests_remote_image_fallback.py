from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from .utils.images import product_image_responsive_meta


class RemoteImageFallbackTests(SimpleTestCase):
    def test_imported_original_used_without_guessing_variant_urls_or_network_calls(self):
        storage = Mock()
        original = 'https://images.example.com/products/imported.jpg'
        field = SimpleNamespace(name='products/imported.jpg', storage=storage, url=original)
        with patch('EcommerceApp.utils.images._is_remote_storage', return_value=True):
            meta = product_image_responsive_meta(field)
        self.assertEqual(meta['src'], original)
        self.assertEqual(meta['preload_src'], original)
        self.assertNotIn('-320w', meta['srcset'])
        storage.exists.assert_not_called()
        storage.open.assert_not_called()
        storage.url.assert_not_called()
