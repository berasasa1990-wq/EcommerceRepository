"""Characterization of current behavior, not the proposed redirect implementation.
Run only with scripts.bera_home_qa_settings (isolated test DB, no external requests).
"""
import html
import json
import re
from pathlib import Path
from urllib.parse import urlencode
from django.core.cache import cache
from django.test import TestCase
from EcommerceApp.models import Brand, Category, Product
from EcommerceApp.cart import Cart

LEGACY = {
 'filter_color': 'light-blue', 'filter_status': 'instock,on_sale',
 'filter_tax_product_cat': '133,141,159,164,166,1529,2058,2078,2096,2447,2455,2481',
 'filter_by_rating': '3', 'add-to-cart': '33218', 'in-stock': '1',
 'on-sale': '1', 'post_type': 'product',
}
GSC_QUERIES = [
 'filter_color=light-blue&filter_status=instock&filter_tax_product_cat=133%2C141%2C159%2C164%2C166%2C1529%2C2058%2C2078%2C2096%2C2447%2C2455%2C2481&in-stock=1&on-sale=1&post_type=product&add-to-cart=33218',
 'filter_by_rating=3&filter_color=light-blue&filter_status=instock%2Con_sale&filter_tax_product_cat=133%2C141%2C159%2C166%2C2058%2C2070%2C2078%2C2096%2C2447&in-stock=1&on-sale=1&post_type=product&add-to-cart=30202',
 'add-to-cart=29264&filter_by_rating=4&filter_color=light-blue&filter_tax_product_cat=142%2C295%2C2066%2C2070%2C2078%2C2092%2C2097%2C2102%2C2412%2C2415%2C2417%2C2444%2C2447%2C2453%2C2456%2C2467%2C2471%2C2484%2C2500&in-stock=1&on-sale=1&post_type=product',
 'filter_color=light-blue&filter_tax_product_cat=137%2C156%2C2066%2C2070%2C2078%2C2102%2C2417%2C2455%2C2467%2C2500&in-stock=1&on-sale=1&post_type=product&add-to-cart=30031',
]

class LegacyURLAudit(TestCase):
    observations = []

    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(naziv='SEO audit kategorija', slug='seo-audit')
        cls.brand = Brand.objects.create(naziv='SEO audit brend', slug='seo-audit')
        cls.product = Product.objects.create(naziv='SEO audit štap', slug='seo-audit-stap', sifra='SEO-001', cijena=42, stanje=8, na_stanju=True, aktivan=True, kategorija=cls.category, brend=cls.brand)
        Product.objects.bulk_create([Product(naziv=f'SEO audit štap {i}',slug=f'seo-audit-{i}',sifra=f'SEO-{i+2:03}',cijena=42,stanje=8,na_stanju=True,aktivan=True,kategorija=cls.category,brend=cls.brand) for i in range(51)])

    def setUp(self):
        cache.clear()

    def fetch(self, path):
        response = self.client.get(path)
        body = response.content.decode('utf-8', 'replace')
        canonicals = re.findall(r'<link rel="canonical" href="([^"]+)"', body)
        robots = re.findall(r'<meta name="robots" content="([^"]+)"', body)
        self.observations.append({'path':path,'status':response.status_code,'location':response.get('Location'),'canonical':[html.unescape(x) for x in canonicals],'robots':robots})
        return response, [html.unescape(x) for x in canonicals]

    def assert_page(self, path, canonical):
        response, tags = self.fetch(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tags, ['https://carpologijabh.ba'+canonical])
        return response

    def test_legacy_parameters_on_all_valid_routes(self):
        for path in ['/', self.category.get_absolute_url(), self.product.get_absolute_url()]:
            for key, value in LEGACY.items():
                with self.subTest(path=path,key=key):
                    self.assert_page(path+'?'+urlencode({key:value}),path)

    def test_combined_gsc_examples(self):
        for query in GSC_QUERIES:
            with self.subTest(query=query):
                self.assert_page('/?'+query,'/')

    def test_unknown_woocommerce_paths_are_404(self):
        for path in ['/shop/','/product/seo-audit-stap/','/product-category/seo-audit/','/seo-audit-unknown/','/artikal/nepostojeci/','/kategorija/nepostojeca/']:
            with self.subTest(path=path):
                response,_=self.fetch(path+'?'+urlencode(LEGACY))
                self.assertEqual(response.status_code,404)
                self.assertIsNone(response.get('Location'))

    def test_root_legacy_add_to_cart_does_not_modify_cart(self):
        before=self.client.session.get(Cart.SESSION_KEY,{})
        self.assert_page('/?add-to-cart='+str(self.product.pk),'/')
        self.assertEqual(self.client.session.get(Cart.SESSION_KEY,{}),before)

    def test_current_cart_endpoint_remains_post_only(self):
        response,_=self.fetch(self.product.get_absolute_url()+'dodaj/')
        self.assertEqual(response.status_code,405)

    def test_clean_routes_have_one_canonical(self):
        for path in ['/',self.category.get_absolute_url(),self.product.get_absolute_url()]:
            self.assert_page(path,path)

    def test_category_pagination_and_out_of_range(self):
        path=self.category.get_absolute_url()
        self.assert_page(path+'?page=1',path)
        self.assert_page(path+'?page=2',path+'?page=2')
        self.assert_page(path+'?page=999999',path+'?page=2')
        self.assert_page(path+'?page=bad',path)

    def test_indexable_brand_pagination(self):
        self.assert_page('/?brend=seo-audit&page=2','/?brend=seo-audit&page=2')

    def test_legacy_plus_current_parameters_preserves_behavior(self):
        response=self.assert_page('/?brend=seo-audit&page=2&filter_color=light-blue','/?brend=seo-audit&page=2')
        self.assertEqual(response.context['page_obj'].number,2)
        response=self.assert_page('/?q=audit&post_type=product','/')
        self.assertContains(response,'<meta name="robots" content="noindex, follow">',html=True)
        self.assertEqual(response.context['filter_params']['q'],'audit')

    def test_search_pagination_noindex(self):
        response=self.assert_page('/?q=audit&page=2','/')
        self.assertContains(response,'<meta name="robots" content="noindex, follow">',html=True)

    def test_plain_home_page_parameter(self):
        self.assert_page('/?page=2','/')

    def test_trailing_slash_redirect_keeps_legacy_parameters(self):
        path=self.product.get_absolute_url()
        response,_=self.fetch(path.rstrip('/')+'?filter_color=light-blue')
        self.assertEqual(response.status_code,301)
        self.assertEqual(response['Location'],path+'?filter_color=light-blue')

    def test_sitemap_has_no_legacy_parameters_and_has_canonical_routes(self):
        response,_=self.fetch('/sitemap.xml')
        self.assertEqual(response.status_code,200)
        from xml.etree import ElementTree as ET
        locs=[x.text for x in ET.fromstring(response.content).iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        self.assertIn('https://carpologijabh.ba'+self.product.get_absolute_url(),locs)
        self.assertIn('https://carpologijabh.ba'+self.category.get_absolute_url(),locs)
        self.assertIn('https://carpologijabh.ba/?brend=seo-audit',locs)
        self.assertTrue(all(x.startswith('https://carpologijabh.ba/') for x in locs))
        self.assertFalse(any(key+'=' in x for key in LEGACY for x in locs))

    def test_robots_sitemap_and_no_global_query_block(self):
        response,_=self.fetch('/robots.txt')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Sitemap: https://carpologijabh.ba/sitemap.xml')
        self.assertNotContains(response,'Disallow: /*?')

    @classmethod
    def tearDownClass(cls):
        Path('/private/tmp/seo-legacy-observations.json').write_text(json.dumps(cls.observations,ensure_ascii=False,indent=2))
        super().tearDownClass()
