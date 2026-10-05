import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .models import Category, Product, ProductVariation, MungosProductMapping
from .mungos_product import build_product_preview
from .mungos_update import build_mungos_update_payload
from .mungos_payload import sanitize_mungos_payload
from .mungos_partial import build_partial_payload


@override_settings(MUNGOS_ENABLED=True, MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                   MUNGOS_API_KEY='test-key', MUNGOS_ECOMMERCE_ACCESS_CODE='test-access', SITE_URL='https://example.com')
class MungosSaleTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(naziv='Mašinice')
        self.product = self.make_product('sale')
        self.http = patch('EcommerceApp.mungos_client.requests.Session')
        self.factory = self.http.start()
        self.addCleanup(self.http.stop)
        self.session = self.factory.return_value.__enter__.return_value
        self.uuid = str(uuid4())
        for method in ('post', 'put'):
            response = getattr(self.session, method).return_value.__enter__.return_value
            response.status_code = 200
            response.iter_content.return_value = [json.dumps({'productUuid': self.uuid}).encode()]
        sleep = patch('EcommerceApp.management.commands.mungos_bulk_sync.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def make_product(self, sku, **fields):
        return Product.objects.create(naziv='Reel ' + sku, sifra=sku, cijena=100, akcijska_cijena=80,
            stanje=10, na_stanju=True, aktivan=True, kategorija=self.category, **fields)

    def run_sale(self, **options):
        output = StringIO()
        call_command('mungos_sale_sync', stdout=output, **options)
        return output.getvalue()

    def test_regular_price_equals_selling_and_other_payload_fields_unchanged(self):
        self.product.akcijska_cijena = None
        preview = build_product_preview(self.product)
        prices = preview['payload']['ProductPrice']
        self.assertEqual((prices['Price'], prices['SellingPrice'], prices['DiscountEndDate']), (100, 100, None))
        self.assertEqual(preview['payload']['price'], 100)
        self.assertEqual(build_partial_payload(preview, 'price')['price'], 100)
        self.assertNotIn('SellingPrice', build_partial_payload(preview, 'price'))

    def test_sale_create_and_update_preserve_regular_and_actual_price(self):
        preview = build_product_preview(self.product)
        original = deepcopy(preview)
        prices = preview['payload']['ProductPrice']
        self.assertEqual(prices, {'Price': 100, 'SellingPrice': 80, 'Currency': None,
            'IsNegotiable': False, 'IsFree': False, 'DiscountEndDate': None})
        self.assertGreater(prices['Price'], prices['SellingPrice'])
        self.assertEqual(build_mungos_update_payload(preview)['payload']['ProductPrice'], prices)
        self.assertEqual(preview, original)
        partial = build_partial_payload(preview, 'price')
        self.assertEqual(partial['price'], 80)
        self.assertNotIn('ProductPrice', partial)
        self.assertNotIn('SellingPrice', partial)

    def test_expired_discount_and_non_sale_are_never_sent(self):
        Product.objects.filter(pk=self.product.pk).update(akcija_do=timezone.localdate() - timedelta(days=1))
        self.product.refresh_from_db()
        prices = build_product_preview(self.product)['payload']['ProductPrice']
        self.assertEqual((prices['Price'], prices['SellingPrice'], prices['DiscountEndDate']), (100, 100, None))
        self.run_sale(confirm=True)
        self.factory.assert_not_called()

    def test_discount_end_date_only_for_winning_active_normal_sale(self):
        self.product.akcija_do = timezone.localdate() + timedelta(days=2)
        self.assertEqual(build_product_preview(self.product)['payload']['ProductPrice']['DiscountEndDate'],
                         self.product.akcija_do.isoformat())
        for flash in (Decimal('60'), Decimal('80')):
            with patch.object(Product, 'flash_sale_price', return_value=flash):
                prices = build_product_preview(self.product)['payload']['ProductPrice']
                self.assertEqual(prices['SellingPrice'], float(flash))
                self.assertIsNone(prices['DiscountEndDate'])
        with patch.object(Product, 'flash_sale_price', return_value=Decimal('90')):
            self.assertEqual(build_product_preview(self.product)['payload']['ProductPrice']['DiscountEndDate'],
                             self.product.akcija_do.isoformat())

    def test_existing_uuid_always_full_update_never_post(self):
        MungosProductMapping.objects.create(product=self.product, mungos_uuid=self.uuid, last_sync_status='REGISTERED')
        output = self.run_sale(product_id=self.product.pk, confirm=True)
        self.session.put.assert_called_once()
        self.session.post.assert_not_called()
        self.assertTrue(self.session.put.call_args.args[0].endswith('/standard/product/' + self.uuid))
        self.assertEqual(self.session.put.call_args.kwargs['json']['ProductPrice']['SellingPrice'], 80)
        for field in ('PRODUCT_ID', 'SKU', 'NAME', 'REGULAR_PRICE', 'SELLING_PRICE', 'DISCOUNT_PERCENT', 'ACTION', 'MUNGOS_UUID'):
            self.assertIn(field, output)
        self.assertIn('UPDATED', output)
        self.assertNotIn('test-key', output)
        self.assertNotIn('test-access', output)

    def test_new_sale_uses_safe_create_saves_mapping_and_next_run_updates(self):
        self.run_sale(confirm=True, limit=1)
        self.session.post.assert_called_once()
        mapping = MungosProductMapping.objects.get(product=self.product)
        self.assertEqual(str(mapping.mungos_uuid), self.uuid)
        self.assertEqual(mapping.last_sync_status, 'CREATED')
        self.run_sale(confirm=True, limit=1)
        self.session.post.assert_called_once()
        self.session.put.assert_called_once()

    def test_dry_run_is_select_only_with_no_mapping_or_http(self):
        with CaptureQueriesContext(connection) as queries:
            output = self.run_sale(limit=5)
        self.assertTrue(all(row['sql'].lstrip().upper().startswith('SELECT') for row in queries))
        self.assertIn('DRY RUN', output)
        self.assertIn('READY_CREATE', output)
        self.factory.assert_not_called()
        self.assertFalse(MungosProductMapping.objects.exists())

    def test_limit_counts_eligible_sale_products_not_scanned_products(self):
        Product.objects.filter(pk=self.product.pk).update(akcijska_cijena=None)
        sale = self.make_product('sale2')
        self.make_product('sale3')
        self.run_sale(limit=1, confirm=True)
        self.session.post.assert_called_once()
        self.assertEqual(self.session.post.call_args.kwargs['json']['id'], sale.sifra)

    def test_unknown_variants_inactive_and_unsupported_category_skip(self):
        MungosProductMapping.objects.create(product=self.product, last_sync_status='UNKNOWN_REMOTE_STATE')
        variant = self.make_product('variant')
        ProductVariation.objects.create(artikal=variant, naziv='Variant', sifra='variant1')
        inactive = self.make_product('inactive')
        Product.objects.filter(pk=inactive.pk).update(aktivan=False)
        unsupported = self.make_product('unsupported')
        Product.objects.filter(pk=unsupported.pk).update(kategorija=None)
        output = self.run_sale(limit=5, confirm=True)
        self.assertIn('SKIPPED', output)
        self.factory.assert_not_called()

    def test_failure_does_not_change_any_webshop_product_and_no_duplicate_create(self):
        before = list(Product.objects.values().order_by('pk'))
        self.session.post.side_effect = requests.Timeout('test-key')
        output = self.run_sale(confirm=True)
        self.assertIn('UNKNOWN_REMOTE_STATE', output)
        self.assertEqual(list(Product.objects.values().order_by('pk')), before)
        self.assertEqual(MungosProductMapping.objects.get(product=self.product).last_sync_status, 'UNKNOWN_REMOTE_STATE')
        self.run_sale(confirm=True)
        self.session.post.assert_called_once()
        self.session.put.assert_not_called()

    def test_invalid_sale_schema_blocks_transport(self):
        baseline = build_product_preview(self.product)['payload']
        for changes in ({'SellingPrice': 110}, {'Price': None}, {'DiscountEndDate': 'invented'},
                        {'Currency': 'BAM'}, {'Extra': True}):
            source = deepcopy(baseline)
            source['ProductPrice'].update(changes)
            _, reasons = sanitize_mungos_payload(source)
            self.assertTrue(reasons)
        self.factory.assert_not_called()

    def test_create_guard_is_persisted_before_http_and_missing_uuid_blocks_repeat(self):
        response = self.session.post.return_value.__enter__.return_value
        response.iter_content.return_value = [b'{}']

        def check_guard(*args, **kwargs):
            mapping = MungosProductMapping.objects.get(product=self.product)
            self.assertEqual(mapping.last_sync_status, 'IN_FLIGHT')
            self.assertIsNone(mapping.mungos_uuid)
            return self.session.post.return_value

        self.session.post.side_effect = check_guard
        self.run_sale(confirm=True)
        mapping = MungosProductMapping.objects.get(product=self.product)
        self.assertEqual(mapping.last_sync_status, 'UNKNOWN_REMOTE_STATE')
        self.run_sale(confirm=True)
        self.session.post.assert_called_once()

    def test_expired_normal_discount_with_current_flash_has_no_borrowed_end_date(self):
        self.product.akcija_do = timezone.localdate() - timedelta(days=1)
        with patch.object(Product, 'flash_sale_price', return_value=Decimal('70')):
            prices = build_product_preview(self.product)['payload']['ProductPrice']
        self.assertEqual((prices['Price'], prices['SellingPrice'], prices['DiscountEndDate']), (100, 70, None))
