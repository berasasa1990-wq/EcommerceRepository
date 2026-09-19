from html.parser import HTMLParser

from django.template import Context
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.test import SimpleTestCase


class PreloadLinks(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'link':
            self.links.append(dict(attrs))


class HomeBannerPreloadTests(SimpleTestCase):
    def links(self, slides=(), **overrides):
        context = {'hero_slides': slides, 'lcp_image_url': '/desktop.jpg',
                   'lcp_image_srcset': '/desktop-640.jpg 640w, /desktop.jpg 1600w',
                   'lcp_image_sizes': '100vw', **overrides}
        # Render the actual head block without unrelated database context processors.
        template = get_template('home.html').template
        blocks = template.nodelist.get_nodes_by_type(BlockNode)
        preload = next(node for node in blocks if node.name == 'lcp_preload')
        render_context = Context(context)
        with render_context.bind_template(template):
            return PreloadLinks(preload.render(render_context)).links

    def test_mobile_and_desktop_preloads_match_picture_sources(self):
        mobile, desktop = self.links([{'has_mobile_image': True, 'has_video': False,
            'image_mobile': '/mobile.jpg', 'image_mobile_srcset': '/mobile-small.jpg 480w, /mobile.jpg 1080w'}])
        self.assertEqual(mobile['href'], '/mobile.jpg')
        self.assertEqual(mobile['imagesrcset'], '/mobile-small.jpg 480w, /mobile.jpg 1080w')
        self.assertEqual(mobile['media'], '(max-width: 768px)')
        self.assertEqual(desktop['media'], 'not all and (max-width: 768px)')
        self.assertEqual(desktop['href'], '/desktop.jpg')
        self.assertEqual(desktop['imagesrcset'], '/desktop-640.jpg 640w, /desktop.jpg 1600w')
        for link in (mobile, desktop):
            self.assertEqual(link['fetchpriority'], 'high')
            self.assertEqual(link['imagesizes'], '100vw')

    def test_mobile_without_srcset_uses_single_mobile_file(self):
        links = self.links([{'has_mobile_image': True, 'image_mobile': '/mobile.jpg'}])
        self.assertEqual(links[0]['href'], '/mobile.jpg')
        self.assertNotIn('imagesrcset', links[0])

    def test_no_mobile_image_keeps_single_unrestricted_preload(self):
        for slides in ([], [{'has_mobile_image': False}]):
            with self.subTest(slides=slides):
                links = self.links(slides)
                self.assertEqual(len(links), 1)
                self.assertNotIn('media', links[0])
                self.assertEqual(links[0]['href'], '/desktop.jpg')

    def test_video_keeps_poster_preload_on_all_screens(self):
        links = self.links([{'has_mobile_image': True, 'has_video': True, 'image_mobile': '/unused.jpg'}])
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]['href'], '/desktop.jpg')
        self.assertNotIn('media', links[0])

    def test_no_lcp_image_does_not_emit_preloads(self):
        self.assertEqual(self.links(lcp_image_url=None), [])
