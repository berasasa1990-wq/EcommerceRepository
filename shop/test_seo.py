import json
import re
from io import BytesIO
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from PIL import Image
from .forms import SEOSettingsForm
from .models import SEOSettings, Product, Category, StoreSettings


class SEOTests(TestCase):
    def setUp(self):
        self.config = SEOSettings.objects.create(pk=1, site_name='Moj Shop', home_title='Najbolja ponuda | Moj Shop', description='Opis naše trgovine.', canonical_domain='https://shop.example.com', google_verification='verification-token')
        self.category = Category.objects.create(name='Oprema', seo_title='Oprema za vas', seo_description='Opis kategorije.')
        self.product = Product.objects.create(name='Test proizvod', price=25, category=self.category, description='<p>Opis proizvoda.</p>')

    def test_home_metadata_canonical_and_structured_data(self):
        response = self.client.get('/')
        self.assertContains(response, '<title>Najbolja ponuda | Moj Shop</title>', html=True)
        self.assertContains(response, 'name="description" content="Opis naše trgovine."')
        self.assertContains(response, 'rel="canonical" href="https://shop.example.com/"')
        self.assertContains(response, 'name="google-site-verification" content="verification-token"')
        schema = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), re.S)[1])
        self.assertEqual(schema[0]['@type'], 'Organization')

    def test_product_category_metadata_and_escaping(self):
        self.product.seo_title = 'Poseban SEO naslov'
        self.product.seo_description = '</script><script>alert(1)</script>'
        self.product.save()
        response = self.client.get(self.product.get_absolute_url())
        self.assertContains(response, '<title>Poseban SEO naslov</title>', html=True)
        self.assertNotContains(response, '</script><script>alert(1)</script>')
        schema = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', response.content.decode(), re.S)[1])
        self.assertEqual(schema[0]['offers']['price'], '25.00')
        self.assertEqual(schema[0]['offers']['priceCurrency'], 'BAM')
        response = self.client.get(self.category.get_absolute_url())
        self.assertContains(response, '<title>Oprema za vas</title>', html=True)
        self.assertContains(response, 'Opis kategorije.')

    def test_private_and_search_pages_noindex(self):
        for path in ('/korpa/', '/checkout/', '/nalog/prijava/', '/panel/prijava/', '/bera-control/', '/?q=test'):
            response = self.client.get(path, follow=True)
            self.assertEqual(response['X-Robots-Tag'], 'noindex, nofollow')
        self.assertNotIn('X-Robots-Tag', self.client.get('/'))
        self.config.allow_indexing = False
        self.config.save()
        self.assertContains(self.client.get('/'), 'content="noindex, nofollow"')
        self.assertEqual(self.client.get('/sitemap.xml').status_code, 404)

    def test_sitemap_excludes_hidden_and_private_urls(self):
        hidden = Product.objects.create(name='Skriveni', price=10, is_active=False)
        response = self.client.get('/sitemap.xml')
        root = ElementTree.fromstring(response.content)
        self.assertTrue(root.tag.endswith('sitemapindex'))
        response = self.client.get(reverse('seo_sitemap_section', kwargs={'section': 'products', 'page': 1}))
        self.assertContains(response, 'https://shop.example.com' + self.product.get_absolute_url())
        self.assertNotContains(response, hidden.get_absolute_url())
        self.assertNotContains(response, '/nalog/')
        self.assertEqual(self.client.get('/sitemap-products-999.xml').status_code, 404)
        response = self.client.get('/robots.txt')
        self.assertContains(response, 'Sitemap: https://shop.example.com/sitemap.xml')
        self.config.sitemap_enabled = False
        self.config.save()
        self.assertNotContains(self.client.get('/robots.txt'), 'Sitemap:')
        self.assertEqual(self.client.get('/sitemap.xml').status_code, 404)

    def test_staff_settings_are_isolated_from_delivery_and_featured_forms(self):
        self.assertEqual(self.client.get('/panel/podesavanje/').status_code, 302)
        staff = get_user_model().objects.create_user('seo-manager', is_staff=True)
        self.client.force_login(staff)
        store = StoreSettings.objects.create(pk=1, delivery_price=9)
        data = {'settings_section': 'seo', 'site_name': 'Novi naziv', 'home_title': 'Novi naslov', 'canonical_domain': 'https://new.example.com', 'allow_indexing': 'on', 'sitemap_enabled': 'on', 'structured_data': 'on'}
        response = self.client.post('/panel/podesavanje/', data)
        self.assertEqual(response.status_code, 302)
        store.refresh_from_db()
        self.assertEqual(store.delivery_price, 9)
        self.config.refresh_from_db()
        self.assertEqual(self.config.site_name, 'Novi naziv')
        self.client.post('/panel/podesavanje/', {'delivery_price': 5, 'free_delivery_threshold': ''})
        self.config.refresh_from_db()
        self.assertEqual(self.config.home_title, 'Novi naslov')
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(staff)
        self.assertEqual(protected.post('/panel/podesavanje/', data).status_code, 403)

    def test_domain_validation(self):
        for domain in ('https://example.com/path', 'https://user:password@example.com', 'javascript:alert(1)', 'https://example.com/?secret=1'):
            form = SEOSettingsForm({'site_name': 'Shop', 'canonical_domain': domain})
            self.assertFalse(form.is_valid())
            self.assertIn('canonical_domain', form.errors)

    def test_social_upload_and_image_metadata(self):
        data = BytesIO()
        Image.new('RGB', (1200, 630), 'blue').save(data, format='PNG')
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            form = SEOSettingsForm({'site_name': 'Shop'}, {'social_image': SimpleUploadedFile('social.png', data.getvalue(), 'image/png')}, instance=self.config)
            self.assertTrue(form.is_valid(), form.errors)
            cfg = form.save()
            self.assertContains(self.client.get('/'), 'property="og:image"')
            self.assertTrue(cfg.social_image.name.startswith('seo/'))
