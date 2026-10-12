import os
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, Resolver404
from .wms_tools import TOOLS
from .models import MagacinPonuda, WMSOrder, Order


class WMSToolsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('tools-wms', 'tools@example.test', 'test-only')
        self.client.force_login(self.user)
        p = patch('EcommerceApp.wms_tools.module_locked', return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_tool_pages_use_wms_shell_and_keep_legacy_views(self):
        for key, title, old, module in TOOLS:
            with self.subTest(tool=key):
                url = '/wms/' + key + '/'
                self.assertTrue(resolve(url).url_name.startswith('staff_magacin_'))
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'wms-legacy-tool')
                self.assertContains(response, 'id="wmsMobileSidebar"')
                self.assertNotContains(response, '<aside class="mg-sidebar">')
                self.assertContains(response, '/wms/zalihe/')
                self.assertContains(response, 'wms-legacy-tools.css')
                folder = os.environ.get('WMS_TOOLS_QA_DIR')
                if folder:
                    target = Path(folder)
                    target.mkdir(parents=True, exist_ok=True)
                    (target / (key + '.html')).write_bytes(response.content)

    def test_offer_post_uses_existing_service_and_stays_in_wms(self):
        response = self.client.post('/wms/kreiraj-ponudu/', {'action': 'nova'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MagacinPonuda.objects.count(), 1)
        self.assertEqual(response['Location'], '/wms/kreiraj-ponudu/%s/' % MagacinPonuda.objects.get().pk)
        self.assertContains(self.client.get(response['Location']), 'wms-legacy-tool')

    def test_permission_checks_and_original_routes_remain(self):
        with patch('EcommerceApp.wms_tools.module_locked', return_value=True):
            self.assertEqual(self.client.get('/wms/uvoz/').status_code, 403)
            response = self.client.get('/wms/uvoz/', HTTP_X_WMS_PERMISSIONS='1')
            self.assertEqual(response.json()['redirect'], '/wms/')
        self.client.logout()
        self.assertEqual(self.client.get('/wms/uvoz/').status_code, 302)
        with self.assertRaises(Resolver404):
            resolve('/nalog/magacin/uvoz/')

    def test_source_number_is_used_in_every_wms_display(self):
        source = Order.objects.create(broj='WEB-12345', ime_prezime='Kupac', ukupno=0)
        order = WMSOrder.objects.create(source_order=source, kupac='Kupac', tip='online')
        self.assertEqual(order.display_number, source.broj)
        response = self.client.get('/wms/narudzbe/pregled/%s/' % order.pk)
        self.assertContains(response, 'Narudžba #WEB-12345')
        from django.template.loader import render_to_string
        printed = render_to_string('bera_reference/staff/wms/orders_print.html', {'kind': 'racun', 'jobs': [{'order': order, 'rows': [], 'total': 0}]})
        self.assertIn('Narudžba #WEB-12345', printed)
        with patch('EcommerceApp.wms_orders.sync_panel_orders'):
            self.assertContains(self.client.get('/wms/narudzbe/'), '#WEB-12345')
        manual = WMSOrder.objects.create(kupac='Ručno', tip='online')
        self.assertEqual(manual.display_number, f'{manual.pk:04d}')

    def test_secondary_stocktake_route_and_json_navigation(self):
        from django.http import JsonResponse
        from django.test import RequestFactory
        from .wms_tools import tool_view
        self.assertTrue(resolve('/wms/popis-robe/test/').url_name.startswith('staff_magacin_'))
        request = RequestFactory().get('/wms/uvoz/')
        request.user = self.user
        request.resolver_match = resolve('/wms/uvoz/')
        def endpoint(request):
            return JsonResponse({'redirect_url': '/nalog/magacin/uvoz/1/', 'note': '/nalog/magacin/uvoz/1/'})
        response = tool_view(endpoint, 'uvoz', 'wms_zalihe', 'staff_magacin_uvoz')(request)
        import json
        data = json.loads(response.content)
        self.assertEqual(data['redirect_url'], '/wms/uvoz/1/')
        self.assertEqual(data['note'], '/nalog/magacin/uvoz/1/')
