from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from PIL import Image, ImageChops, ImageDraw

from .catalog_images import catalog_card_image_meta, fit_catalog_artwork


class CatalogImageFitTests(SimpleTestCase):
    def picture(self, size, bounds):
        image = Image.new('RGB', size, 'white')
        ImageDraw.Draw(image).rectangle(bounds, fill='#23452b')
        return image

    def test_different_empty_margins_produce_same_visible_product_size(self):
        examples = [self.picture((200, 200), (50, 30, 149, 169)),
                    self.picture((600, 600), (250, 230, 349, 369))]
        boxes = []
        for image in examples:
            result = fit_catalog_artwork(image)
            boxes.append(ImageChops.difference(result, Image.new('RGB', result.size, 'white')).getbbox())
        self.assertEqual(boxes[0], boxes[1])
        self.assertEqual(result.size, (600, 600))

    def test_colour_and_product_proportions_are_retained(self):
        image = Image.new('RGBA', (500, 500))
        ImageDraw.Draw(image).rectangle((100, 200, 399, 299), fill=(255, 0, 0, 255))
        result = fit_catalog_artwork(image)
        bounds = ImageChops.difference(result, Image.new('RGB', result.size, 'white')).getbbox()
        self.assertGreater((bounds[2] - bounds[0]) / (bounds[3] - bounds[1]), 2.8)
        self.assertEqual(result.getpixel((300, 300)), (255, 0, 0))

    def test_generated_files_preserve_original_and_are_reused(self):
        with TemporaryDirectory() as root:
            storage = FileSystemStorage(location=root, base_url='/media/')
            data = BytesIO()
            self.picture((600, 600), (240, 220, 359, 379)).save(data, format='PNG')
            original = data.getvalue()
            name = storage.save('products/test.png', ContentFile(original))
            field = SimpleNamespace(name=name, storage=storage)
            meta = catalog_card_image_meta(field)
            self.assertIn('-card-fit-v1-320w.webp', meta['src'])
            self.assertIn('600w', meta['srcset'])
            with storage.open(name) as source:
                self.assertEqual(source.read(), original)
            with patch.object(storage, 'open', side_effect=AssertionError('Original must be cached')):
                self.assertEqual(catalog_card_image_meta(field), meta)
            cache.clear()
            with patch.object(storage, 'open', side_effect=AssertionError('Existing variants must be reused')):
                self.assertEqual(catalog_card_image_meta(field), meta)

    def test_card_template_uses_normalized_artwork(self):
        with TemporaryDirectory() as root:
            storage = FileSystemStorage(location=root, base_url='/media/')
            data = BytesIO()
            self.picture((500, 500), (200, 150, 299, 349)).save(data, format='PNG')
            name = storage.save('products/card.png', ContentFile(data.getvalue()))
            product = SimpleNamespace(prikazna_slika=SimpleNamespace(name=name, storage=storage), naziv='Torba')
            html = render_to_string('partials/product_image.html', {'product': product})
            self.assertIn('-card-fit-v1-320w.webp', html)
            self.assertIn('width="600" height="600"', html)
            self.assertIn('data-main-image', html)
