import ast
import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import (Order, OrderItem, OrderStockHold, Product, ProductVariation,
                     WarehouseLocation, WarehouseStock, WarehouseSyncLog)
from .warehouse_audit import read_only_snapshot, warehouse_snapshot


@override_settings(SITE_PREP_ENABLED=False, TURNSTILE_SECRET_KEY='', TURNSTILE_SITE_KEY='',
    STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
              'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class OdooDetachTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Lokalni artikal', sifra='LOCAL-1', barkod='387000001',
            cijena=12, stanje=8, na_stanju=True, magacin_sync_at=timezone.now(), odoo_template_id=101)
        self.variant = ProductVariation.objects.create(artikal=self.product, naziv='Crvena', sifra='LOCAL-1-R',
            cijena=12, stanje=8, na_stanju=True, odoo_variant_id=201)
        self.location = WarehouseLocation.objects.create(sifra='A01', naziv='A01', odoo_location_id=301,
            odoo_location_path='WH/A01')
        self.stock = WarehouseStock.objects.create(product=self.product, variation=self.variant,
            location=self.location, kolicina=8, rezervisano=2)
        self.order = Order.objects.create(ime_prezime='Kupac', ukupno=24, odoo_sale_order_id=401)
        self.item = OrderItem.objects.create(narudzba=self.order, artikal=self.product, varijacija=self.variant,
            naziv='Crvena', cijena=12, kolicina=2)
        OrderStockHold.objects.create(narudzba=self.order, product=self.product, variation=self.variant,
            location=self.location, kolicina=2)
        WarehouseSyncLog.objects.create(izvor='Odoo', status='u_toku', poruka='Historical unfinished job')

    def test_audit_is_deterministic_local_only_and_uses_existing_availability(self):
        before = warehouse_snapshot()
        with patch.dict(os.environ, {}, clear=True), \
             patch('xmlrpc.client.ServerProxy', side_effect=AssertionError('RPC forbidden')), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')):
            first, second = StringIO(), StringIO()
            call_command('audit_odoo_detach', stdout=first)
            call_command('audit_odoo_detach', stdout=second)
        self.assertEqual(first.getvalue(), second.getvalue())
        after = json.loads(first.getvalue())
        self.assertEqual(before, after)
        self.assertEqual(after['warehouse']['availability'][f'{self.product.pk}:{self.variant.pk}'], 6)
        self.assertEqual(after['warehouse']['hold_quantity'], 2)
        for name in ['Product', 'ProductVariation', 'WarehouseStock', 'WarehouseLocation', 'Order', 'OrderItem', 'OrderStockHold']:
            self.assertEqual(after['tables'][f'EcommerceApp.{name}']['count'], 1)

    def test_read_only_guard_rejects_writes_and_restores_connection(self):
        with read_only_snapshot():
            with self.assertRaises(OperationalError):
                WarehouseStock.objects.filter(pk=self.stock.pk).update(kolicina=999)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.kolicina, 8)
        # Restored guard permits ordinary later test/business transactions.
        WarehouseStock.objects.filter(pk=self.stock.pk).update(kolicina=9)

    def test_compare_fails_for_any_quantity_location_reservation_or_product_change(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'before.json'
            path.write_text(json.dumps(warehouse_snapshot()))
            report = StringIO()
            call_command('audit_odoo_detach', compare=str(path), stdout=report)
            self.assertTrue(json.loads(report.getvalue())['comparison']['identical'])
            self.stock.kolicina += 1
            self.stock.save(update_fields=['kolicina'])
            report = StringIO()
            with self.assertRaises(CommandError):
                call_command('audit_odoo_detach', compare=str(path), stdout=report)
            self.assertIn('EcommerceApp.WarehouseStock', json.loads(report.getvalue())['comparison']['changed_tables'])

    def test_legacy_sync_and_sale_actions_cannot_change_existing_local_state(self):
        user = User.objects.create_superuser('detach-admin', 'detach@example.com', 'pass')
        self.client.force_login(user)
        before = warehouse_snapshot()
        with patch('xmlrpc.client.ServerProxy', side_effect=AssertionError('RPC forbidden')), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')):
            self.assertEqual(self.client.post('/nalog/magacin/sync/', {'action': 'stock'}).status_code, 404)
            response = self.client.post(reverse('staff_order_detail', args=[self.order.broj]), {'action': 'odoo_narudzba'})
            self.assertIn(response.status_code, (200, 302))
            self.assertEqual(self.client.post(reverse('staff_order_brza_posta', args=[self.order.broj]),
                                             {'action': 'skini_stanje'}).status_code, 200)
            # Existing pending historical jobs do not resume merely by visiting the warehouse.
            session = self.client.session
            session['magacin_sync_job'] = {'log_id': WarehouseSyncLog.objects.get().pk, 'done': False}
            session.save()
            for name in ['staff_magacin_artikli', 'staff_magacin_podesavanja']:
                url = reverse(name, args=[self.order.broj]) if name.endswith('detail') else reverse(name)
                self.assertEqual(self.client.get(url).status_code, 200)
        after = warehouse_snapshot()
        self.assertEqual([key for key in before['tables'] if before['tables'][key] != after['tables'][key]], [])
        with self.assertRaises(NoReverseMatch):
            reverse('admin:EcommerceApp_product_odoo_import')
        with self.assertRaises(CommandError):
            call_command('sync_odoo_magacin')

    def test_picking_has_local_location_and_keeps_all_historical_identifiers(self):
        from .views import _build_order_packing_lines
        from .views_magacin import _order_pick_bundle
        before = warehouse_snapshot()
        with patch('xmlrpc.client.ServerProxy', side_effect=AssertionError('RPC forbidden')):
            lines, error = _build_order_packing_lines(self.order)
            queue, _, _ = _order_pick_bundle(self.order)
        self.assertFalse(error)
        self.assertEqual(lines[0]['picks'][0]['location_id'], self.location.pk)
        self.assertTrue(queue)
        self.assertEqual(before, warehouse_snapshot())
        self.product.refresh_from_db(); self.variant.refresh_from_db(); self.location.refresh_from_db()
        self.assertEqual((self.product.odoo_template_id, self.variant.odoo_variant_id, self.location.odoo_location_id), (101, 201, 301))

    def test_fingerprint_detects_changes_to_every_required_table(self):
        # Each mutation is on a simulated test database and rolled back between subtests.
        from django.db import transaction
        cases = [(Product, self.product.pk, {'sifra': 'CHANGED'}),
                 (ProductVariation, self.variant.pk, {'sifra': 'CHANGED'}),
                 (WarehouseStock, self.stock.pk, {'rezervisano': 3}),
                 (WarehouseLocation, self.location.pk, {'naziv': 'CHANGED'}),
                 (Order, self.order.pk, {'napomena': 'CHANGED'}),
                 (OrderItem, self.item.pk, {'kolicina': 3}),
                 (OrderStockHold, OrderStockHold.objects.get().pk, {'kolicina': 3})]
        before = warehouse_snapshot()
        for model, pk, changes in cases:
            with self.subTest(model=model.__name__), transaction.atomic():
                model.objects.filter(pk=pk).update(**changes)
                after = warehouse_snapshot()
                self.assertNotEqual(before['fingerprint'], after['fingerprint'])
                self.assertNotEqual(before['tables'][model._meta.label], after['tables'][model._meta.label])
                transaction.set_rollback(True)
        self.assertEqual(before, warehouse_snapshot())


class OdooRuntimeIsolationTests(TestCase):
    def test_no_remote_clients_or_credentials_in_application_code(self):
        root = Path(settings.BASE_DIR)
        forbidden = {'odoo_client', 'odoo_import', 'odoo_sales', 'xmlrpc', 'jsonrpc'}
        for directory in ['EcommerceApp', 'EcommerceProject']:
            for path in (root / directory).rglob('*.py'):
                if 'migrations' in path.parts or path.name.startswith('tests'):
                    continue
                source = path.read_text()
                tree = ast.parse(source)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        imports = [node.module or '']
                    else:
                        continue
                    for name in imports:
                        self.assertFalse(set(name.split('.')) & forbidden, str(path))
                for key in ['ODOO_URL', 'ODOO_DB', 'ODOO_USERNAME', 'ODOO_API_KEY']:
                    self.assertNotIn(key, source, str(path))

    def test_fresh_startup_without_any_odoo_variables(self):
        script = '''
import os
from pathlib import Path
from unittest.mock import patch
original = Path.read_text
def read_text(path, *args, **kwargs):
    value = original(path, *args, **kwargs)
    if path.name == '.env':
        value = '\\n'.join(line for line in value.splitlines() if not line.lstrip().startswith('ODOO_'))
    return value
os.environ['DJANGO_SETTINGS_MODULE'] = 'EcommerceProject.settings'
with patch.object(Path, 'read_text', read_text), patch('xmlrpc.client.ServerProxy', side_effect=AssertionError('RPC forbidden')):
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    assert not any(name.startswith('ODOO_') for name in os.environ)
    assert not hasattr(settings, 'ODOO_URL')
    call_command('check')
'''
        environment = {key: value for key, value in os.environ.items() if not key.startswith('ODOO_')}
        result = subprocess.run([sys.executable, '-c', script], cwd=settings.BASE_DIR,
                                env=environment, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
