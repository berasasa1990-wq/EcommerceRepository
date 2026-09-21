import json
import re
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from .models import Category, Product
from .utils.seo import absolute_url


@override_settings(
    SITE_URL='https://render-host.onrender.com',
    ALLOWED_HOSTS=['testserver', 'carpologijabh.ba', 'opremazaribolov.ba', 'www.opremazaribolov.ba'],
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class SeoDomainTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(naziv='Štapovi', slug='stapovi')
        self.product = Product.objects.create(
            naziv='Test štap', slug='test-stap', sifra='TEST-STAP',
            cijena=Decimal('42.00'), aktivan=True, na_stanju=True,
            kategorija=self.category,
        )

    def canonical(self, response):
        match = re.search(rb'<link rel="canonical" href="([^"]+)"', response.content)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_home_product_and_category_use_apex_canonical(self):
        for path in ('/', self.category.get_absolute_url(), self.product.get_absolute_url()):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.canonical(response), 'https://carpologijabh.ba' + path)
            self.assertNotIn('render-host.onrender.com', response.content.decode())
        home = self.client.get('/')
        self.assertEqual(len(re.findall(rb'<h1\b', home.content)), 1)
        self.assertContains(home, 'Carpologija BH')

    def test_product_schema_and_breadcrumbs_use_canonical_domain(self):
        response = self.client.get(self.product.get_absolute_url())
        blocks = re.findall(rb'<script type="application/ld\+json">(.*?)</script>', response.content, re.S)
        data = [json.loads(block) for block in blocks]
        product = next(block for block in data if block.get('@type') == 'Product')
        breadcrumb = next(block for block in data if block.get('@type') == 'BreadcrumbList')
        self.assertEqual(product['url'], 'https://carpologijabh.ba' + self.product.get_absolute_url())
        self.assertEqual(product['offers']['priceCurrency'], 'BAM')
        self.assertTrue(all(item['item'].startswith('https://carpologijabh.ba/') for item in breadcrumb['itemListElement']))

    def test_sitemap_and_robots_use_new_domain_even_on_old_host(self):
        sitemap = self.client.get('/sitemap.xml', HTTP_HOST='opremazaribolov.ba')
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn(b'https://carpologijabh.ba/', sitemap.content)
        self.assertNotIn(b'https://opremazaribolov.ba/', sitemap.content)
        self.assertNotIn(b'onrender.com', sitemap.content)
        robots = self.client.get('/robots.txt')
        self.assertContains(robots, 'Sitemap: https://carpologijabh.ba/sitemap.xml')

    def test_media_domain_and_old_domain_support_remain(self):
        url = 'https://media.opremazaribolov.ba/example.jpg'
        self.assertEqual(absolute_url(url), url)
        self.assertIn('opremazaribolov.ba', settings.ALLOWED_HOSTS)
        self.assertIn('www.opremazaribolov.ba', settings.ALLOWED_HOSTS)

    def test_search_and_filter_duplicates(self):
        search = self.client.get('/?q=stap')
        self.assertContains(search, '<meta name="robots" content="noindex, follow">', html=True)
        brand = self.client.get('/?akcija=1')
        self.assertEqual(self.canonical(brand), 'https://carpologijabh.ba/?akcija=1')

        child = Category.objects.create(naziv='Feeder štapovi', slug='feeder-stapovi', roditelj=self.category)
        self.product.kategorija = child
        self.product.save(update_fields=['kategorija'])
        cache.clear()
        category = self.client.get(self.category.get_absolute_url())
        all_products = self.client.get(self.category.get_absolute_url() + '?all=1')
        self.assertEqual(self.canonical(category), 'https://carpologijabh.ba' + self.category.get_absolute_url())
        self.assertEqual(self.canonical(all_products), 'https://carpologijabh.ba' + self.category.get_absolute_url() + '?all=1')
