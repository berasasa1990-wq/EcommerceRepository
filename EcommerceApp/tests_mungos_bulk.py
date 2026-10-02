import json
from io import StringIO
from unittest.mock import patch
from uuid import UUID

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from .models import Category, Product, ProductVariation, MungosProductMapping
from .mungos_client import MungosError
from .management.commands.mungos_bulk_sync import retry_seconds

REMOTE = 'a9241b59-9e45-4840-9730-c93cb8ad9517'


@override_settings(MUNGOS_ENABLED=True, MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                   MUNGOS_API_KEY='private-api-secret', MUNGOS_ECOMMERCE_ACCESS_CODE='private-access-secret',
                   SITE_URL='https://carpologijabh.ba')
class BulkTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(naziv='Štapovi')
        self.product = Product.objects.create(pk=4455, naziv='MATE M8', sifra='7889',
                                              cijena=100, stanje=8, kategorija=self.category)
        self.client_patch = patch('EcommerceApp.management.commands.mungos_bulk_sync.MungosClient')
        self.client = self.client_patch.start().return_value
        self.addCleanup(self.client_patch.stop)
        self.client.send_product.return_value = (200, json.dumps({'productUuid': REMOTE}), False)
        self.client.update_product.return_value = (200, '{}', False)
        self.client.retry_after = '2'
        self.sleep_patch = patch('EcommerceApp.management.commands.mungos_bulk_sync.time.sleep')
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def run_bulk(self, *args):
        output = StringIO()
        call_command('mungos_bulk_sync', *args, stdout=output)
        return output.getvalue()

    def mapping(self):
        return MungosProductMapping.objects.create(product=self.product, mungos_uuid=REMOTE)

    def second(self):
        return Product.objects.create(naziv='Second', sifra='SECOND', cijena=20, kategorija=self.category)

    def test_dry_run_no_http_or_writes(self):
        with patch('requests.sessions.Session.request') as http:
            output = self.run_bulk()
        http.assert_not_called()
        self.client.send_product.assert_not_called()
        self.assertFalse(MungosProductMapping.objects.exists())
        self.assertIn('READY_CREATE', output)

    def test_all_without_confirm_no_http(self):
        self.run_bulk('--all')
        self.client.send_product.assert_not_called()

    def test_limit(self):
        self.second()
        output = self.run_bulk('--limit', '1')
        self.assertIn('"TOTAL": 1', output)

    def test_all(self):
        self.second()
        self.assertIn('"TOTAL": 2', self.run_bulk('--all'))

    def test_start_after_id(self):
        other = self.second()
        output = self.run_bulk('--start-after-id', str(self.product.pk))
        self.assertNotIn('"product_id": 4455', output)
        self.assertIn(f'"product_id": {other.pk}', output)

    def test_confirm_create_persists_uuid(self):
        self.run_bulk('--confirm')
        self.assertEqual(MungosProductMapping.objects.get().mungos_uuid, UUID(REMOTE))
        self.client.send_product.assert_called_once()
        self.client.update_product.assert_not_called()

    def test_mate_existing_mapping_update_never_post(self):
        self.mapping()
        self.run_bulk('--confirm')
        self.client.send_product.assert_not_called()
        self.assertEqual(self.client.update_product.call_args.args[0], REMOTE)
        self.assertEqual(MungosProductMapping.objects.get().last_sync_status, 'UPDATED')

    def test_same_product_second_run_update(self):
        self.run_bulk('--confirm')
        self.run_bulk('--confirm')
        self.client.send_product.assert_called_once()
        self.client.update_product.assert_called_once()

    def test_missing_uuid_blocks_resume(self):
        self.client.send_product.return_value = (200, '{}', False)
        self.run_bulk('--confirm')
        row = MungosProductMapping.objects.get()
        self.assertIsNone(row.mungos_uuid)
        self.assertEqual(row.last_sync_status, 'UNKNOWN_REMOTE_STATE')
        self.run_bulk('--confirm')
        self.client.send_product.assert_called_once()

    def test_truncated_uuid_blocks(self):
        self.client.send_product.return_value = (200, json.dumps({'productUuid': REMOTE}), True)
        self.run_bulk('--confirm')
        self.assertIsNone(MungosProductMapping.objects.get().mungos_uuid)

    def test_invalid_uuid_blocks(self):
        self.client.send_product.return_value = (200, '{"productUuid":"bad"}', False)
        self.run_bulk('--confirm')
        self.assertIsNone(MungosProductMapping.objects.get().mungos_uuid)

    def test_post_timeout_no_retry_and_resume_blocked(self):
        self.client.send_product.side_effect = MungosError('UNKNOWN_REMOTE_STATE | timeout')
        self.run_bulk('--confirm')
        self.run_bulk('--confirm')
        self.client.send_product.assert_called_once()

    def test_put_timeout_no_retry(self):
        self.mapping()
        self.client.update_product.side_effect = MungosError('UNKNOWN_REMOTE_STATE | timeout')
        self.run_bulk('--confirm')
        self.run_bulk('--confirm')
        self.client.update_product.assert_called_once()
        self.client.send_product.assert_not_called()

    def test_guard_committed_before_http(self):
        def send(payload):
            self.assertEqual(MungosProductMapping.objects.get().last_sync_status, 'IN_FLIGHT')
            raise KeyboardInterrupt
        self.client.send_product.side_effect = send
        with self.assertRaises(KeyboardInterrupt):
            self.run_bulk('--confirm')
        self.client.send_product.side_effect = None
        self.run_bulk('--confirm')
        self.client.send_product.assert_called_once()

    def test_inflight_resume_skips(self):
        row = self.mapping()
        row.last_sync_status = 'IN_FLIGHT'
        row.save()
        self.run_bulk('--confirm')
        self.client.update_product.assert_not_called()

    def test_category_skip(self):
        self.product.kategorija = Category.objects.create(naziv='Nepoznato')
        self.product.save()
        self.assertIn('missing_category', self.run_bulk('--confirm'))
        self.client.send_product.assert_not_called()

    def test_variant_skip(self):
        ProductVariation.objects.create(artikal=self.product, naziv='Varijanta', sifra='VAR', cijena=50)
        self.assertIn('variants_not_supported', self.run_bulk('--confirm'))
        self.client.send_product.assert_not_called()

    def test_invalid_sku_skip(self):
        Product.objects.filter(pk=self.product.pk).update(sifra='')
        self.assertIn('invalid_sku', self.run_bulk('--confirm'))
        self.client.send_product.assert_not_called()

    def test_inactive_and_hidden_skip(self):
        for field in ('aktivan', 'sakriven_do_stanja'):
            Product.objects.filter(pk=self.product.pk).update(aktivan=True, sakriven_do_stanja=False)
            Product.objects.filter(pk=self.product.pk).update(**{field: field != 'aktivan'})
            self.assertIn('not_publishable', self.run_bulk('--confirm'))
        self.client.send_product.assert_not_called()

    def test_individual_http_failures_continue(self):
        self.second()
        for status in (400, 404, 409, 500):
            with self.subTest(status=status):
                MungosProductMapping.objects.all().delete()
                self.client.send_product.reset_mock()
                self.client.send_product.return_value = (status, '{}', False)
                output = self.run_bulk('--all', '--confirm')
                self.assertEqual(self.client.send_product.call_count, 2)
                self.assertIn(f'api_{status}', output)
                if status == 500:
                    self.assertTrue(all(row.last_sync_status == 'UNKNOWN_REMOTE_STATE' for row in MungosProductMapping.objects.all()))

    def test_auth_stops(self):
        self.second()
        for status in (401, 403):
            MungosProductMapping.objects.all().delete()
            self.client.send_product.reset_mock()
            self.client.send_product.return_value = (status, '{}', False)
            with self.assertRaises(CommandError):
                self.run_bulk('--all', '--confirm')
            self.client.send_product.assert_called_once()

    def test_post_429_no_retry(self):
        self.client.send_product.return_value = (429, '{}', False)
        self.run_bulk('--confirm')
        self.client.send_product.assert_called_once()
        self.sleep.assert_called_with(2)
        self.assertEqual(MungosProductMapping.objects.get().last_sync_status, 'UNKNOWN_REMOTE_STATE')

    def test_put_429_bounded_retry(self):
        self.mapping()
        self.client.update_product.return_value = (429, '{}', False)
        self.run_bulk('--confirm')
        self.assertEqual(self.client.update_product.call_count, 3)
        self.assertEqual(MungosProductMapping.objects.get().last_sync_status, 'FAILED')

    def test_put_429_then_success(self):
        self.mapping()
        self.client.update_product.side_effect = [(429, '{}', False), (200, '{}', False)]
        self.run_bulk('--confirm')
        self.assertEqual(self.client.update_product.call_count, 2)
        self.assertEqual(MungosProductMapping.objects.get().last_sync_status, 'UPDATED')

    def test_delay(self):
        other = self.second()
        self.mapping()
        MungosProductMapping.objects.create(product=other, mungos_uuid='12345678-1234-1234-1234-123456789012')
        self.run_bulk('--all', '--confirm', '--delay', '2')
        self.sleep.assert_called_once()
        self.assertGreater(self.sleep.call_args.args[0], 1.9)

    def test_invalid_delay(self):
        for value in ('0', 'nan', 'inf'):
            with self.assertRaises(CommandError):
                self.run_bulk('--delay', value)

    @override_settings(MUNGOS_BASE_URL='https://mungos.ba/api/v1/connector')
    def test_invalid_host_stops(self):
        with self.assertRaises(CommandError):
            self.run_bulk('--confirm')
        self.client.send_product.assert_not_called()

    def test_secrets_redacted(self):
        Product.objects.filter(pk=self.product.pk).update(sifra='private-api-secret')
        output = self.run_bulk('--confirm')
        self.assertNotIn('private-api-secret', output)
        self.assertNotIn('private-access-secret', output)

    def test_mapping_command_no_http_idempotent(self):
        with patch('requests.sessions.Session.request') as http:
            call_command('mungos_mapping_set', self.product.pk, REMOTE, stdout=StringIO())
            call_command('mungos_mapping_set', self.product.pk, REMOTE, stdout=StringIO())
        http.assert_not_called()
        self.assertEqual(MungosProductMapping.objects.count(), 1)

    def test_mapping_command_rejects_reassignment(self):
        self.mapping()
        with self.assertRaises(CommandError):
            call_command('mungos_mapping_set', self.product.pk, '12345678-1234-1234-1234-123456789012')

    def test_mapping_command_resolves_uncertainty(self):
        MungosProductMapping.objects.create(product=self.product, last_sync_status='IN_FLIGHT')
        call_command('mungos_mapping_set', self.product.pk, REMOTE, stdout=StringIO())
        self.run_bulk('--confirm')
        self.client.update_product.assert_called_once()

    def test_retry_after_parser(self):
        self.assertEqual(retry_seconds('3'), 3)
        self.assertEqual(retry_seconds('bad'), 0)
        self.assertEqual(retry_seconds('nan'), 0)

    def test_webshop_source_unchanged(self):
        before = Product.objects.values().get(pk=self.product.pk)
        self.run_bulk('--confirm')
        self.assertEqual(Product.objects.values().get(pk=self.product.pk), before)

    def test_invalid_price_skip(self):
        from unittest.mock import PropertyMock
        with patch.object(Product, 'prikazna_cijena', new_callable=PropertyMock, return_value=float('nan')):
            self.assertIn('invalid_price', self.run_bulk('--confirm'))
        self.client.send_product.assert_not_called()

    def test_auth_during_response_read_stops(self):
        self.second()
        self.client.send_product.side_effect = MungosError('UNKNOWN_REMOTE_STATE | read', http_status=401)
        with self.assertRaises(CommandError):
            self.run_bulk('--all', '--confirm')
        self.client.send_product.assert_called_once()

    def test_global_config_failure_no_http(self):
        self.client_patch.stop()
        with override_settings(MUNGOS_ENABLED=False):
            with self.assertRaises(CommandError):
                self.run_bulk('--confirm')
        self.client.send_product.assert_not_called()

    def test_bad_scope_rejected(self):
        with self.assertRaises(CommandError):
            self.run_bulk('--limit', '0')
        with self.assertRaises(CommandError):
            self.run_bulk('--start-after-id', '-1')

    def test_retry_after_http_date(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
        self.assertGreater(retry_seconds(value), 28)

    def test_mapping_uuid_unique(self):
        self.mapping()
        other = self.second()
        with self.assertRaises(CommandError):
            call_command('mungos_mapping_set', other.pk, REMOTE, stdout=StringIO())

    def test_response_headers_captured_by_real_client(self):
        from .mungos_client import MungosClient
        from .mungos_product import build_product_preview
        with patch('EcommerceApp.mungos_client.requests.Session') as session:
            response = session.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code = 429
            response.headers = {'Retry-After': '4'}
            response.iter_content.return_value = [b'{}']
            client = MungosClient()
            result = client.send_product(build_product_preview(self.product)['payload'])
        self.assertEqual(result[0], 429)
        self.assertEqual(client.retry_after, '4')
