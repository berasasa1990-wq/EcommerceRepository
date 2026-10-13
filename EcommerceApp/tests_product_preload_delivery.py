from html.parser import HTMLParser
from types import SimpleNamespace

from django.template import Context
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.test import SimpleTestCase


class ProductPreloadDeliveryTests(SimpleTestCase):
    def preload(self, image, lcp_image_url='/media/product-original.jpg'):
        template = get_template('bera_reference/product_detail.html').template
        block = next(node for node in template.nodelist.get_nodes_by_type(BlockNode)
                     if node.name == 'lcp_preload')
        context = Context({'product': SimpleNamespace(prikazna_slika_responsive=image),
                           'lcp_image_url': lcp_image_url})
        with context.bind_template(template):
            html = block.render(context)
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links = []
            def handle_starttag(self, tag, attrs):
                if tag == 'link':
                    self.links.append(dict(attrs))
        parser = Links()
        parser.feed(html)
        return parser.links

    def test_preload_selects_same_responsive_candidate_as_main_image(self):
        image = {'src': '/media/product-400w.jpg',
                 'srcset': '/media/product-400w.jpg 400w, /media/product-800w.jpg 800w, /media/product-original.jpg 1200w'}
        link, = self.preload(image)
        self.assertEqual(link['href'], image['src'])
        self.assertEqual(link['imagesrcset'], image['srcset'])
        self.assertEqual(link['imagesizes'], '(max-width: 768px) 92vw, 50vw')
        self.assertEqual(link['fetchpriority'], 'high')

    def test_original_only_storage_does_not_advertise_unmeasured_widths(self):
        link, = self.preload({'src': 'https://media.example.com/product.avif', 'srcset': ''})
        self.assertEqual(link['href'], 'https://media.example.com/product.avif')
        self.assertNotIn('imagesrcset', link)
        self.assertNotIn('imagesizes', link)

    def test_missing_metadata_preserves_original_fallback(self):
        link, = self.preload(None)
        self.assertEqual(link['href'], '/media/product-original.jpg')

    def test_missing_lcp_image_does_not_preload_a_placeholder(self):
        self.assertEqual(self.preload(None, lcp_image_url=None), [])
