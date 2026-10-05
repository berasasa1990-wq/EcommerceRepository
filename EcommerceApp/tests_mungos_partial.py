from io import StringIO
from unittest.mock import patch, call
from copy import deepcopy

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.db import connection
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext

from .models import Category, Product, MungosProductMapping, ProductVariation
from .mungos_partial import PRICE_FIELDS, build_partial_payload
from .mungos_client import MungosError
from .mungos_product import build_product_preview

UUID = 'a9241b59-9e45-4840-9730-c93cb8ad9517'


@override_settings(MUNGOS_ENABLED=True, MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                   MUNGOS_API_KEY='test-key', MUNGOS_ECOMMERCE_ACCESS_CODE='test-access',
                   SITE_URL='https://example.com')
class MungosPartialTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Reel', sifra='existing-reference', cijena=138,
            opis='Opis', stanje=43, aktivan=True, na_stanju=True,
            kategorija=Category.objects.create(naziv='Mašinice'))
        self.mapping = MungosProductMapping.objects.create(product=self.product, mungos_uuid=UUID,
                                                          last_sync_status='REGISTERED')
        self.http = patch('EcommerceApp.mungos_client.requests.Session')
        self.factory = self.http.start()
        self.addCleanup(self.http.stop)
        self.session = self.factory.return_value.__enter__.return_value
        self.response = self.session.put.return_value.__enter__.return_value
        self.response.status_code = 200
        self.response.iter_content.return_value = [b'{}']

    def run_sync(self, operation, confirm=False):
        output = StringIO()
        call_command('mungos_' + operation + '_sync', product_id=self.product.pk,
                     confirm=confirm, stdout=output)
        return output.getvalue()

    def test_dry_run_is_select_only_and_has_actual_payload(self):
        for operation in ('price', 'quantity'):
            with CaptureQueriesContext(connection) as queries:
                output = self.run_sync(operation)
            self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
            self.assertIn('DRY RUN', output)
            self.assertIn('existing-reference', output)
        self.factory.assert_not_called()

    def test_exact_payloads_reuse_builder_without_mutation(self):
        preview = build_product_preview(self.product)
        original = deepcopy(preview)
        for operation in ('price', 'quantity'):
            expected = ({key: preview['payload'].get(key) for key in PRICE_FIELDS} if operation == 'price'
                        else {'id': 'existing-reference', 'quantity': preview['payload']['quantityRemaining']})
            self.assertEqual(build_partial_payload(preview, operation), expected)
            self.session.put.reset_mock()
            self.run_sync(operation, True)
            self.session.put.assert_called_once_with(
                'https://staging.mungos.ba/api/v1/connector/standard/product/' + UUID + ('/quantity' if operation == 'quantity' else ''),
                json=expected, headers={'X-Api-Key': 'test-key', 'ecommerceaccesscode': 'test-access'},
                timeout=(5, 10), allow_redirects=False, stream=True)
            self.session.post.assert_not_called()
            self.mapping.refresh_from_db()
            self.assertEqual(self.mapping.last_sync_status, 'UPDATED')
        self.assertEqual(preview, original)
        self.product.refresh_from_db()
        self.assertEqual((self.product.cijena, self.product.stanje, self.product.sifra),
                         (138, 43, 'existing-reference'))

    def test_vendor_sale_and_normal_prices_on_product_put_without_local_writes(self):
        for selling in (15, None):
            Product.objects.filter(pk=self.product.pk).update(cijena=20, akcijska_cijena=selling)
            before = list(Product.objects.values())
            self.session.put.reset_mock()
            self.run_sync('price', True)
            request = self.session.put.call_args
            self.assertTrue(request.args[0].endswith('/standard/product/' + UUID))
            prices = request.kwargs['json']['ProductPrice']
            self.assertEqual(prices, {'Price': 20.0, 'SellingPrice': 15.0 if selling else 20.0,
                'Currency': None, 'IsNegotiable': False, 'IsFree': False, 'DiscountEndDate': None})
            self.assertEqual(list(Product.objects.values()), before)
            self.session.post.assert_not_called()

    def test_legacy_price_only_and_missing_productprice_never_reach_http(self):
        from .mungos_client import MungosClient
        client = MungosClient()
        payload = build_partial_payload(build_product_preview(self.product), 'price')
        missing = {key: value for key, value in payload.items() if key != 'ProductPrice'}
        one_price = {'id': self.product.sifra, 'price': 15.0}
        for body in (missing, one_price):
            for method in (client.sync_price, client.update_product, client.send_product):
                with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
                    if method == client.send_product:
                        method(body)
                    else:
                        method(UUID, body)
        with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
            client._write_partial(UUID, one_price, 'price')
        self.factory.assert_not_called()

    def test_unknown_unmapped_and_inflight_are_skipped(self):
        for operation in ('price', 'quantity'):
            for status in ('UNKNOWN', 'UNKNOWN_REMOTE_STATE', 'IN_FLIGHT'):
                self.mapping.last_sync_status = status
                self.mapping.save()
                self.assertIn('SKIPPED', self.run_sync(operation, True))
            self.mapping.last_sync_status = 'REGISTERED'
            self.mapping.mungos_uuid = None
            self.mapping.save()
            self.assertIn('SKIPPED', self.run_sync(operation, True))
        self.mapping.delete()
        self.assertIn('SKIPPED', self.run_sync('price', True))
        self.factory.assert_not_called()

    def test_variants_are_skipped(self):
        ProductVariation.objects.create(artikal=self.product, naziv='Variant', sifra='variant', cijena=10)
        for operation in ('price', 'quantity'):
            self.assertIn('SKIPPED', self.run_sync(operation, True))
        self.factory.assert_not_called()

    def test_error_responses_never_retry_or_create(self):
        for operation in ('price', 'quantity'):
            for status in (400, 401, 403, 404, 409, 302, 500):
                self.mapping.last_sync_status = 'REGISTERED'
                self.mapping.save()
                self.session.put.reset_mock()
                self.response.status_code = status
                with self.assertRaisesMessage(CommandError, f'HTTP status: {status}'):
                    self.run_sync(operation, True)
                self.session.put.assert_called_once()
                self.session.post.assert_not_called()
                self.mapping.refresh_from_db()
                self.assertEqual(str(self.mapping.mungos_uuid), UUID)
                self.assertEqual(self.mapping.last_sync_status,
                                 'UNKNOWN_REMOTE_STATE' if status in (302, 500) else 'FAILED')

    def test_timeout_persists_unknown_and_next_run_skips(self):
        self.session.put.side_effect = requests.Timeout('secret')
        with self.assertRaisesMessage(CommandError, 'UNKNOWN_REMOTE_STATE'):
            self.run_sync('quantity', True)
        self.assertIn('SKIPPED', self.run_sync('price', True))
        self.session.put.assert_called_once()
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.last_sync_status, 'UNKNOWN_REMOTE_STATE')

    def test_disabled_or_production_configuration_never_sends_or_claims(self):
        for config in ({'MUNGOS_ENABLED': False}, {'MUNGOS_ECOMMERCE_ACCESS_CODE': ''},
                       {'MUNGOS_BASE_URL': 'https://mungos.ba/api/v1/connector'}):
            with override_settings(**config), self.assertRaises(CommandError):
                self.run_sync('price', True)
        self.factory.assert_not_called()
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.last_sync_status, 'REGISTERED')

    def test_current_sale_price_and_cart_availability_are_reused(self):
        Product.objects.filter(pk=self.product.pk).update(akcijska_cijena=99, stanje=17)
        self.run_sync('price', True)
        self.assertEqual(self.session.put.call_args.kwargs['json']['price'], 99.0)
        with patch('EcommerceApp.mungos_product.Cart.availability', return_value={'parent': 6}) as availability:
            self.run_sync('quantity', True)
        availability.assert_called_once()
        self.assertEqual(self.session.put.call_args.kwargs['json'],
                         {'id': 'existing-reference', 'quantity': 6})

    def test_invalid_partial_bodies_and_uuids_never_reach_transport(self):
        from .mungos_client import MungosClient, MungosError
        client = MungosClient()
        preview = build_product_preview(self.product)
        for operation in ('price', 'quantity'):
            valid = build_partial_payload(preview, operation)
            method = getattr(client, 'sync_' + operation)
            for invalid in ({}, {**valid, 'invented': 1}, {**valid, 'id': ''},
                            {**valid, operation: -1}, {**valid, operation: True},
                            {**valid, operation: float('nan')}):
                with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
                    method(UUID, invalid)
            with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
                method('../bad', valid)
        self.factory.assert_not_called()

    def make_mapped_product(self, number, status='REGISTERED', mapped=True):
        product = Product.objects.create(naziv=f'Reel {number}', sifra=f'ref-{number}', cijena=20 + number,
            opis='Opis', stanje=number, aktivan=True, na_stanju=True, kategorija=self.product.kategorija)
        if mapped:
            MungosProductMapping.objects.create(product=product,
                mungos_uuid=f'00000000-0000-4000-8000-{number:012d}', last_sync_status=status)
        return product

    def run_bulk(self, operation, **options):
        output = StringIO()
        call_command('mungos_' + operation + '_sync', stdout=output, **options)
        return output.getvalue()

    def test_bulk_both_operations_and_delay_with_chunked_iterator(self):
        second = self.make_mapped_product(2)
        third = self.make_mapped_product(3)
        real_iterator = QuerySet.iterator
        for operation in ('price', 'quantity'):
            self.session.put.reset_mock()
            with patch('EcommerceApp.mungos_partial_command.time.monotonic', return_value=10), \
                 patch('EcommerceApp.mungos_partial_command.time.sleep') as sleep, \
                 patch('django.db.models.query.QuerySet.iterator', autospec=True) as iterator:
                iterator.side_effect = real_iterator
                self.run_bulk(operation, confirm=True, delay=2)
                iterator.assert_called_once()
                self.assertEqual(iterator.call_args.kwargs['chunk_size'], 200)
            self.assertEqual(self.session.put.call_count, 3)
            self.assertEqual(sleep.call_args_list, [call(2), call(2)])
            self.assertEqual([call.kwargs['json']['id'] for call in self.session.put.call_args_list],
                             ['existing-reference', second.sifra, third.sifra])
            for request in self.session.put.call_args_list:
                mapping = MungosProductMapping.objects.get(product__sifra=request.kwargs['json']['id'])
                suffix = '/' + str(mapping.mungos_uuid) + ('/quantity' if operation == 'quantity' else '')
                self.assertTrue(request.args[0].endswith(suffix))
            self.session.post.assert_not_called()

    def test_bulk_scope_limit_resume_and_single_product(self):
        second = self.make_mapped_product(2)
        self.make_mapped_product(3)
        for operation in ('price', 'quantity'):
            self.session.put.reset_mock()
            self.run_bulk(operation, confirm=True, start_after_id=self.product.pk, limit=1)
            self.session.put.assert_called_once()
            self.assertEqual(self.session.put.call_args.kwargs['json']['id'], second.sifra)
            self.session.put.reset_mock()
            self.run_bulk(operation, confirm=True, product_id=second.pk)
            self.session.put.assert_called_once()
            self.assertEqual(self.session.put.call_args.kwargs['json']['id'], second.sifra)

    def test_bulk_dry_run_select_only_skips_unknown_unmapped_variants(self):
        self.make_mapped_product(2, mapped=False)
        self.make_mapped_product(3, status='UNKNOWN_REMOTE_STATE')
        self.make_mapped_product(4, status='IN_FLIGHT')
        variant = self.make_mapped_product(5)
        ProductVariation.objects.create(artikal=variant, naziv='Variant', sifra='bulk-variant', cijena=10)
        for operation in ('price', 'quantity'):
            with CaptureQueriesContext(connection) as queries:
                output = self.run_bulk(operation)
            self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
            self.assertIn('DRY RUN', output)
            self.assertIn('existing-reference', output)
            self.assertIn('SKIPPED', output)
            self.assertNotIn('ref-2', output)
            self.assertNotIn('ref-3', output)
        self.factory.assert_not_called()

    def test_bulk_failure_continues_preserves_catalog_and_never_retries(self):
        second = self.make_mapped_product(2)
        before = list(Product.objects.values().order_by('pk'))
        with patch('EcommerceApp.mungos_partial_command.time.sleep'), \
             patch('EcommerceApp.mungos_partial_command.MungosClient.sync_quantity',
                   side_effect=[MungosError(
                       'UNKNOWN_REMOTE_STATE | Timeout'), (200, '{}', False)]) as sync:
            with self.assertRaisesMessage(CommandError, '1 neuspješnih'):
                self.run_bulk('quantity', confirm=True)
        self.assertEqual(sync.call_count, 2)
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.last_sync_status, 'UNKNOWN_REMOTE_STATE')
        self.assertEqual(MungosProductMapping.objects.get(product=second).last_sync_status, 'UPDATED')
        self.assertEqual(list(Product.objects.values().order_by('pk')), before)
        self.session.post.assert_not_called()

    def test_bulk_invalid_options_no_http_or_claim(self):
        for options in ({'delay': 0.9}, {'delay': float('nan')}, {'delay': float('inf')},
                        {'limit': 0}, {'start_after_id': -1}, {'product_id': 0}):
            with self.assertRaises(CommandError):
                self.run_bulk('price', confirm=True, **options)
        self.factory.assert_not_called()

    def test_put_429_three_attempts_and_retry_after(self):
        for operation in ('price', 'quantity'):
            self.mapping.last_sync_status = 'REGISTERED'
            self.mapping.save()
            self.session.put.reset_mock()
            self.response.status_code = 429
            self.response.headers = {'Retry-After': '2'}
            with patch('EcommerceApp.mungos_partial_command.time.sleep') as sleep:
                with self.assertRaisesMessage(CommandError, 'HTTP status: 429'):
                    self.run_sync(operation, True)
            self.assertEqual(self.session.put.call_count, 3)
            self.assertEqual(sum(c == call(2) for c in sleep.call_args_list), 3)
            self.session.post.assert_not_called()

    def test_put_retry_success(self):
        with patch('EcommerceApp.mungos_partial_command.MungosClient.sync_price',
                   side_effect=[(429, '{}', False), (200, '{}', False)]) as sync, \
             patch('EcommerceApp.mungos_partial_command.time.sleep'):
            self.run_sync('price', True)
        self.assertEqual(sync.call_count, 2)
        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.last_sync_status, 'UPDATED')

    def test_bulk_auth_stops_immediately_both_endpoints(self):
        self.make_mapped_product(2)
        for operation in ('price', 'quantity'):
            for status in (401, 403):
                self.mapping.last_sync_status = 'REGISTERED'
                self.mapping.save()
                self.session.put.reset_mock()
                self.response.status_code = status
                with self.assertRaisesMessage(CommandError, f'HTTP status: {status}'):
                    self.run_bulk(operation, confirm=True)
                self.session.put.assert_called_once()
                self.session.post.assert_not_called()

    def test_http_diagnostics_redacted_and_source_unchanged(self):
        import json
        before = list(Product.objects.values())
        for operation in ('price', 'quantity'):
            for status in (400, 404, 409):
                self.mapping.last_sync_status = 'REGISTERED'
                self.mapping.save()
                self.response.status_code = status
                self.response.iter_content.return_value = [json.dumps({
                    'errors': {'price': ['Validation rejected']},
                    'Authorization': 'Bearer arbitrary-token',
                    'detail': 'test-key test-access',
                }).encode()]
                output = StringIO()
                with self.assertRaises(CommandError):
                    call_command('mungos_' + operation + '_sync', product_id=self.product.pk,
                                 confirm=True, stdout=output)
                self.assertIn('Validation rejected', output.getvalue())
                for secret in ('test-key', 'test-access', 'arbitrary-token'):
                    self.assertNotIn(secret, output.getvalue())
                self.assertEqual(list(Product.objects.values()), before)
                self.session.post.assert_not_called()

    def test_quantity_reservations_and_negative_outbound(self):
        from .models import WarehouseStock, WarehouseLocation
        location = WarehouseLocation.objects.create(sifra='PARTIAL', naziv='Partial test')
        stock = WarehouseStock.objects.create(product=self.product, location=location,
                                               kolicina=9, rezervisano=4)
        self.run_sync('quantity', True)
        self.assertEqual(self.session.put.call_args.kwargs['json']['quantity'], 5)
        stock.refresh_from_db()
        self.assertEqual((stock.kolicina, stock.rezervisano), (9, 4))
        with patch('EcommerceApp.mungos_product.Cart.availability', return_value={'parent': -8}):
            self.run_sync('quantity', True)
        self.assertEqual(self.session.put.call_args.kwargs['json']['quantity'], 0)

    def test_nonpublishable_and_invalid_sources_blocked(self):
        for changes in ({'aktivan': False}, {'sakriven_do_stanja': True}, {'sifra': ''}):
            Product.objects.filter(pk=self.product.pk).update(aktivan=True, sakriven_do_stanja=False,
                                                             sifra='existing-reference')
            Product.objects.filter(pk=self.product.pk).update(**changes)
            for operation in ('price', 'quantity'):
                self.assertIn('SKIPPED', self.run_sync(operation, True))
        self.factory.assert_not_called()
