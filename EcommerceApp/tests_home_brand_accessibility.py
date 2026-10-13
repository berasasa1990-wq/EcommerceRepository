from html.parser import HTMLParser

from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse


class BrandMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class HomeBrandAccessibilityTests(SimpleTestCase):
    def test_all_brands_keep_native_links_without_list_roles(self):
        brands = [
            {'slug': 'as-obrenovac', 'naziv': 'AS Obrenovac', 'slika': {'url': '/media/as.avif'}},
            {'slug': 'fox', 'naziv': 'FOX', 'slika': {'url': '/media/fox.avif'}},
        ]
        html = render_to_string('partials/home_brand_carousel.html', {'brands': brands})
        elements = BrandMarkup(html).elements
        self.assertEqual([tag for tag, attrs in elements], ['div', 'a', 'img', 'a', 'img'])
        self.assertEqual(elements[0][1], {'class': 'home-brand-grid'})
        self.assertFalse(any(attrs.get('role') in ('list', 'listitem') for tag, attrs in elements))
        links = [attrs for tag, attrs in elements if tag == 'a']
        images = [attrs for tag, attrs in elements if tag == 'img']
        self.assertEqual(len(links), len(brands))
        for brand, link, image in zip(brands, links, images):
            self.assertEqual(link, {
                'href': f"{reverse('home')}?brend={brand['slug']}#product-showcase",
                'class': 'showcase-brand-link home-brand-grid__item',
                'title': brand['naziv'],
            })
            self.assertEqual(image['src'], brand['slika']['url'])
            self.assertEqual(image['alt'], brand['naziv'])
            self.assertEqual(image['class'], 'showcase-brand-logo')

    def test_empty_brands_do_not_render_grid(self):
        html = render_to_string('partials/home_brand_carousel.html', {'brands': []})
        self.assertEqual(BrandMarkup(html).elements, [])
