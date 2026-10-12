"""Regression coverage for retired staff entry points and shared WMS operations."""
import json
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse, Resolver404
from .models import Order, OrderItem, Product
from .warehouse_access import warehouse_landing
from .wms_service_urls import urlpatterns as service_routes

class LegacyRouteRetirementTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser('route-owner', 'owner@example.test', 'test-password')
        self.customer = get_user_model().objects.create_user('route-customer', 'customer@example.test', 'test-password')
        self.product = Product.objects.create(naziv='Test artikal', cijena='12.00', stanje=3, aktivan=True)
        self.order = Order.objects.create(korisnik=self.customer, broj='ACCOUNT-RETIRE', ime_prezime='Test kupac', ukupno='12.00')
        OrderItem.objects.create(narudzba=self.order, artikal=self.product, naziv='Test artikal', cijena='12.00', kolicina=1)

    def test_every_retired_route_is_unavailable_to_anonymous_and_owner(self):
        routes = json.loads((Path(__file__).resolve().parent.parent / 'reports/cleanup/retired-warehouse-routes.json').read_text())
        import re
        for user in (None, self.owner):
            if user: self.client.force_login(user)
            for row in routes:
                url = re.sub(r'<[^>]+>', '1', row['path'])
                with self.subTest(user=bool(user), url=url):
                    with self.assertRaises(Resolver404): resolve(url)
                    self.assertEqual(self.client.get(url).status_code, 404)
            self.client.logout()

    def test_account_login_logout_and_customer_order_are_preserved(self):
        self.assertEqual(resolve('/nalog/').url_name, 'account')
        self.assertEqual(self.client.get('/nalog/').status_code, 302)
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        self.client.force_login(self.customer)
        self.assertContains(self.client.get('/nalog/'), 'ACCOUNT-RETIRE')
        self.assertEqual(self.client.get(reverse('account_order_detail', args=[self.order.broj])).status_code, 200)
        other = get_user_model().objects.create_user('other-customer', password='test-password')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('account_order_detail', args=[self.order.broj])).status_code, 404)
        self.client.post(reverse('logout'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_wms_panel_admin_and_shared_editor_open_without_old_links(self):
        self.client.force_login(self.owner)
        with patch('EcommerceApp.wms_tools.module_locked', return_value=False):
            for url in ['/wms/', '/panel/', '/admin/', reverse('staff_magacin_narudzba_nova'), reverse('staff_magacin_artikli'), reverse('staff_order_detail', args=[self.order.broj])]:
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, '/nalog/magacin/')
                    self.assertNotContains(response, '/nalog/admin/')
        self.assertEqual(warehouse_landing(self.owner), 'staff_wms')
        self.assertEqual(warehouse_landing(self.customer), 'account')

    def test_shared_routes_have_one_canonical_definition_and_reverse(self):
        from .urls import urlpatterns
        routes = [getattr(entry.pattern, '_route', '') for entry in urlpatterns]
        names = [entry.name for entry in urlpatterns if entry.name]
        for entry in service_routes:
            with self.subTest(name=entry.name):
                self.assertEqual(routes.count(entry.pattern._route), 1)
                self.assertEqual(names.count(entry.name), 1)
                parameters = {key: '00000000-0000-0000-0000-000000000001' if converter.__class__.__name__ == 'UUIDConverter' else 1 for key, converter in entry.pattern.converters.items()}
                url = reverse(entry.name, kwargs=parameters)
                self.assertTrue(url.startswith('/wms/'))
                self.assertEqual(resolve(url).url_name, entry.name)

    def test_shared_services_keep_permissions_for_customer_and_anonymous(self):
        for user in (None, self.customer):
            if user: self.client.force_login(user)
            for name in ('staff_magacin_narudzba_nova', 'staff_magacin_barkod_provjera', 'staff_magacin_draft_history'):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)
            self.client.logout()

    def test_every_shared_get_is_renderable_or_has_an_expected_guard(self):
        self.client.force_login(self.owner)
        with patch('EcommerceApp.wms_tools.module_locked', return_value=False):
            for entry in service_routes:
                parameters = {key: 'missing-route-test' if converter.__class__.__name__ in {'StringConverter', 'SlugConverter'} else 999999 for key, converter in entry.pattern.converters.items()}
                url = reverse(entry.name, kwargs=parameters)
                with self.subTest(name=entry.name):
                    response = self.client.get(url)
                    self.assertIn(response.status_code, (200, 302, 400, 403, 404, 405, 410))
                    if response.status_code == 200 and response.get('Content-Type', '').startswith('text/html'):
                        self.assertNotContains(response, '/nalog/magacin/')
                        self.assertNotContains(response, '/nalog/admin/')

    def test_literal_template_links_are_registered(self):
        import re
        from django.urls import get_resolver, URLPattern
        names = set()
        def collect(entries, prefix=''):
            for entry in entries:
                if isinstance(entry, URLPattern):
                    if entry.name: names.add(prefix + entry.name)
                else:
                    collect(entry.url_patterns, prefix + (entry.namespace + ':' if entry.namespace else ''))
        collect(get_resolver().url_patterns)
        folder = Path(__file__).resolve().parent / 'template'
        for template in folder.rglob('*.html'):
            for tag in re.findall(r'{%\s*url\s+.*?%}', template.read_text()):
                match = re.match(r"{%\s*url\s+['\"]([^'\"]+)['\"]", tag)
                if not match or re.search(r'\s+as\s+\w+\s*%}', tag): continue
                self.assertIn(match.group(1), names, str(template))

    def test_every_existing_account_subroute_keeps_its_path_and_name(self):
        import re
        rows = json.loads((Path(__file__).resolve().parent.parent / 'reports/cleanup/preserved-account-routes.json').read_text())
        for row in rows:
            url = re.sub(r'<[^>]+>', '1', row['path'])
            with self.subTest(url=url):
                self.assertEqual(resolve(url).url_name, row['name'])
