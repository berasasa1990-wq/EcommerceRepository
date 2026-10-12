"""Regression audit of rendered site chrome; uses isolated QA database."""
from collections import Counter
from html.parser import HTMLParser
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from types import SimpleNamespace
from .cart import Cart
from .models import Product, Category, Brand


class ChromeParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class HeaderFooterAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(naziv='Audit kategorija', slug='audit-kategorija')
        cls.brand = Brand.objects.create(naziv='Audit brend', slug='audit-brend')
        cls.product = Product.objects.create(naziv='Audit proizvod', slug='audit-proizvod', cijena=20,
                                            stanje=5, na_stanju=True, kategorija=cls.category, brend=cls.brand)
        cls.user = get_user_model().objects.create_superuser('chrome-audit', 'audit@example.test', 'test-only')

    def check_chrome(self, response, headers=1, footers=1):
        self.assertEqual(response.status_code, 200)
        tags = ChromeParser(response.content.decode()).tags
        self.assertEqual(sum(t == 'header' and a.get('id') == 'header' for t, a in tags), headers)
        self.assertEqual(sum(t == 'footer' and 'bera-footer' in a.get('class', '').split() for t, a in tags), footers)
        for kind in ('css', 'js'):
            resources = [a['href'] for t, a in tags if t == 'link' and a.get('rel') == 'stylesheet'] if kind == 'css' else [a['src'] for t, a in tags if t == 'script' and 'src' in a]
            self.assertEqual([url for url, count in Counter(resources).items() if count > 1], [], kind)

    def test_public_pages_have_one_header_footer(self):
        for url in ('/', '/korpa/', '/brendovi/', '/artikal/audit-proizvod/', '/kategorija/audit-kategorija/'):
            with self.subTest(url=url):
                self.check_chrome(self.client.get(url))

    def test_authenticated_account_and_checkout(self):
        self.client.force_login(self.user)
        self.check_chrome(self.client.get('/nalog/'))
        session = self.client.session
        Cart(SimpleNamespace(session=session)).add(self.product)
        session.save()
        response = self.client.get('/narudzba/', follow=True)
        self.check_chrome(response)

    def test_admin_and_panel_are_separate_shells(self):
        self.client.force_login(self.user)
        self.check_chrome(self.client.get('/panel/'), headers=0)
        response = self.client.get(reverse('admin:index'))
        self.check_chrome(response, headers=1, footers=0)
        self.assertNotContains(response, 'bera-home.css')
