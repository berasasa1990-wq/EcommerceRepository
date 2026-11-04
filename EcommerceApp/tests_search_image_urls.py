from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from .views import _suggest_thumb_url


class SearchImageUrlTests(SimpleTestCase):
    def test_replaced_avif_uses_real_original_url_without_inventing_thumbnail(self):
        image = SimpleNamespace(name='products/chair-new.avif',
            url='https://media.example/products/chair-new.avif', storage=Mock())
        self.assertEqual(_suggest_thumb_url(image), image.url)
        image.storage.url.assert_not_called()
        image.storage.exists.assert_not_called()

    def test_non_avif_original_keeps_its_format(self):
        image = SimpleNamespace(name='products/chair.png', url='/media/products/chair.png')
        self.assertEqual(_suggest_thumb_url(image), '/media/products/chair.png')

    def test_missing_image_returns_empty_url(self):
        self.assertEqual(_suggest_thumb_url(None), '')
        self.assertEqual(_suggest_thumb_url(SimpleNamespace(name='')), '')
