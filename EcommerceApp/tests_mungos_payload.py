from copy import deepcopy
from decimal import Decimal
from io import StringIO
import json
from unittest.mock import PropertyMock, patch
import requests

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from .models import Category, Product, ProductImage, ProductVariation
from .mungos_client import MungosClient, MungosError
from .mungos_payload import CREATE_FIELDS, UPDATE_FIELDS, sanitize_mungos_ean, sanitize_mungos_payload
from .mungos_product import build_product_preview, sanitized_json
from .mungos_update import build_mungos_update_payload
from .tests_mungos_update import EXPECTED_PUT


def create_example():
    return {**{key: deepcopy(value) for key, value in EXPECTED_PUT.items() if key != 'brandCode'},
            'HasVariants': False, 'Variants': []}


class MungosPayloadTests(SimpleTestCase):
    def validate(self, **changes):
        return sanitize_mungos_payload({**create_example(), **changes})

    def test_confirmed_create_schema_and_values(self):
        source = create_example()
        result, reasons = sanitize_mungos_payload(source)
        self.assertEqual(reasons, [])
        self.assertEqual(result, source)
        self.assertEqual(set(result), set(CREATE_FIELDS))

    def test_confirmed_update_schema_and_values(self):
        source = deepcopy(EXPECTED_PUT)
        result, reasons = sanitize_mungos_payload(source, 'update')
        self.assertEqual(reasons, [])
        self.assertEqual(result, source)
        self.assertEqual(set(result), set(UPDATE_FIELDS))
        self.assertNotIn('HasVariants', result)
        self.assertNotIn('Variants', result)

    def test_ean8_preserved(self):
        self.assertEqual(sanitize_mungos_ean('96385074'), '96385074')

    def test_ean13_preserved(self):
        self.assertEqual(sanitize_mungos_ean('4006381333931'), '4006381333931')

    def test_invalid_ean_never_repaired(self):
        for value in (None, '', '14587589654', 'abcdefgh', '1234567a', 12345678,
                      ' 96385074', '96385074 ', '１２３４５６７８', '12345678\n', '123456789012', '12345678901234', '٤٠٠٦٣٨١٣٣٣٩٣١'):
            with self.subTest(value=value):
                self.assertEqual(sanitize_mungos_ean(value), '')

    def test_invalid_ean8_checksum_becomes_empty(self):
        self.assertEqual(sanitize_mungos_ean('96385075'), '')

    def test_invalid_ean13_checksum_becomes_empty(self):
        self.assertEqual(sanitize_mungos_ean('4006381333932'), '')

    def test_create_and_update_share_ean_checksum_validation(self):
        for operation, template in (('create', create_example()), ('update', EXPECTED_PUT)):
            for value, expected in (
                ('96385074', '96385074'), ('96385075', ''),
                ('4006381333931', '4006381333931'), ('4006381333932', ''),
                ('12345678901', ''), ('123456789012', ''), ('12345678901234', ''),
                ('１２３４５６７８', ''), ('٤٠٠٦٣٨١٣٣٣٩٣١', ''), ('', ''), (None, ''),
            ):
                with self.subTest(operation=operation, value=value):
                    source = {**deepcopy(template), 'ean': value}
                    original = deepcopy(source)
                    payload, reasons = sanitize_mungos_payload(source, operation)
                    self.assertEqual(reasons, [])
                    self.assertEqual(payload['ean'], expected)
                    self.assertEqual(source, original)

    def test_decimal_and_zero_price(self):
        for value in (Decimal('138.25'), Decimal('0.00'), 0, 138.25):
            with self.subTest(value=value):
                result, reasons = self.validate(price=value)
                self.assertEqual(reasons, [])
                self.assertEqual(result['price'], float(value))
                json.dumps(result, allow_nan=False)

    def test_invalid_price_blocks_and_has_safe_json(self):
        for value in (None, '', '138.00', -1, True, float('nan'), float('inf'),
                      Decimal('NaN'), Decimal('Infinity'), Decimal('sNaN')):
            with self.subTest(value=value):
                result, reasons = self.validate(price=value)
                self.assertTrue(reasons)
                self.assertIsNone(result['price'])
                json.dumps(result, allow_nan=False)

    def test_negative_integer_quantity_clamped_only_outbound(self):
        source = {**create_example(), 'quantityRemaining': -43}
        result, reasons = sanitize_mungos_payload(source)
        self.assertEqual(reasons, [])
        self.assertEqual(result['quantityRemaining'], 0)
        self.assertEqual(source['quantityRemaining'], -43)

    def test_invalid_quantity_blocks(self):
        for value in (None, '', '43', 1.5, 43.0, Decimal('43'), True):
            with self.subTest(value=value):
                result, reasons = self.validate(quantityRemaining=value)
                self.assertTrue(reasons)
                self.assertIsNone(result['quantityRemaining'])

    def test_missing_or_invalid_identity_and_name_block(self):
        for field in ('id', 'sku', 'name'):
            for value in (None, '', '  ', 7889, False, 'bad\nvalue'):
                with self.subTest(field=field, value=value):
                    self.assertTrue(self.validate(**{field: value})[1])

    def test_mismatched_id_and_sku_block(self):
        self.assertTrue(self.validate(id='another')[1])

    def test_missing_or_unconfirmed_category_blocks(self):
        for value in (None, '', 'Reels', 'unknown', []):
            with self.subTest(value=value):
                self.assertTrue(self.validate(categoryCode=value)[1])
        self.assertEqual(self.validate(categoryUuid=None)[1], [])
        self.assertTrue(self.validate(categoryUuid='invented')[1])

    def test_missing_description_is_empty_without_generation(self):
        for value in (None, ''):
            with self.subTest(value=value):
                result, reasons = self.validate(shortDescription=value, details=value)
                self.assertEqual(reasons, [])
                self.assertEqual((result['shortDescription'], result['details']), ('', ''))
        self.assertTrue(self.validate(details=42)[1])

    def test_missing_image_stays_empty_without_invention(self):
        result, reasons = self.validate(images=[])
        self.assertEqual(reasons, [])
        self.assertEqual(result['images'], [])

    def test_multiple_images_main_flag_deduplication_and_source_unchanged(self):
        source = create_example()
        source['images'] = [
            {'imageUrl': 'https://example.com/first.jpg', 'isMainImage': False},
            {'imageUrl': 'https://example.com/second.jpg', 'isMainImage': True},
            {'imageUrl': 'https://example.com/first.jpg', 'isMainImage': True},
        ]
        original = deepcopy(source)
        result, reasons = sanitize_mungos_payload(source)
        self.assertEqual(reasons, [])
        self.assertEqual([row['isMainImage'] for row in result['images']], [True, False])
        self.assertEqual([row['imageUrl'] for row in result['images']],
                         ['https://example.com/first.jpg', 'https://example.com/second.jpg'])
        self.assertEqual(source, original)

    def test_unsafe_or_malformed_images_block(self):
        for value in ('/relative.jpg', 'javascript:evil', 'https://user:pass@example.com/a',
                      'https://example.com/a?token=secret', 'https://example.com/a#fragment',
                      'https://example.com:invalid/a', 'https://example.com/a\nb'):
            with self.subTest(url=value):
                self.assertTrue(self.validate(images=[{'imageUrl': value, 'isMainImage': True}])[1])
        for rows in (None, {}, [{'imageUrl': 'https://example.com/a'}],
                     [{'imageUrl': 'https://example.com/a', 'isMainImage': 'true'}]):
            with self.subTest(images=rows):
                self.assertTrue(self.validate(images=rows)[1])

    def test_fixed_fields_and_unconfirmed_commercial_values_block(self):
        for field, value in (
            ('currencyIsoCode', 'EUR'), ('condition', 'Used'), ('countryCode', 'RS'),
            ('cityCode', 'Sarajevo'), ('warrantyMonthsCount', 12), ('returnDaysCount', 30),
            ('exchangeAcceptable', True), ('productAttributes', {'Color': 'red'}),
            ('isFree', True), ('hasQuantities', 1), ('sellerPaysForReturnShipping', False),
        ):
            with self.subTest(field=field):
                self.assertTrue(self.validate(**{field: value})[1])

    def test_variants_block_both_schemas(self):
        for changes in ({'HasVariants': True}, {'Variants': [{'sku': 'V'}]}):
            self.assertTrue(self.validate(**changes)[1])
            preview = {'status': 'READY_FOR_REVIEW', 'reviewReasons': [],
                       'variantCount': 1, 'payload': {**create_example(), **changes}}
            result = build_mungos_update_payload(preview)
            self.assertEqual(result['status'], 'NEEDS_REVIEW')
            self.assertIsNone(result['payload'])

    def test_unknown_or_missing_fields_and_non_object_body_block(self):
        self.assertTrue(self.validate(extraCreateField=True)[1])
        source = create_example()
        del source['condition']
        self.assertTrue(sanitize_mungos_payload(source)[1])
        for source in (None, '', []):
            self.assertTrue(sanitize_mungos_payload(source)[1])

    def test_update_none_preview_blocks_without_crash(self):
        result = build_mungos_update_payload({'status': 'NEEDS_REVIEW', 'reviewReasons': [], 'payload': None})
        self.assertEqual(result['status'], 'NEEDS_REVIEW')
        self.assertIsNone(result['payload'])

    @override_settings(MUNGOS_ENABLED=True, MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                       MUNGOS_API_KEY='fake-key', MUNGOS_ECOMMERCE_ACCESS_CODE='fake-access')
    def test_transport_revalidates_invalid_payloads_before_any_http(self):
        with patch('EcommerceApp.mungos_client.requests.Session') as factory:
            client = MungosClient()
            for field, value in (('price', -1), ('sku', ''), ('categoryCode', None), ('name', None)):
                with self.subTest(field=field):
                    with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
                        client.send_product({**create_example(), field: value})
                    with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
                        client.update_product('a9241b59-9e45-4840-9730-c93cb8ad9517', {**EXPECTED_PUT, field: value})
            factory.assert_not_called()

    @override_settings(MUNGOS_ENABLED=True, MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                       MUNGOS_API_KEY='fake-key', MUNGOS_ECOMMERCE_ACCESS_CODE='fake-access')
    def test_none_and_non_string_configuration_blocks_before_http(self):
        with patch('EcommerceApp.mungos_client.requests.Session') as factory:
            for field in ('MUNGOS_BASE_URL', 'MUNGOS_API_KEY', 'MUNGOS_ECOMMERCE_ACCESS_CODE'):
                for value in (None, 42):
                    with self.subTest(field=field, value=value), override_settings(**{field: value}):
                        with self.assertRaises(MungosError):
                            MungosClient()
            factory.assert_not_called()

    def test_requests_transport_default_has_no_retries(self):
        # Inspect adapter configuration without sending or mocking a network call.
        with requests.Session() as session:
            self.assertEqual(session.get_adapter('https://staging.mungos.ba').max_retries.total, 0)

    @override_settings(MUNGOS_ENABLED=True, MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
                       MUNGOS_API_KEY='fake-key', MUNGOS_ECOMMERCE_ACCESS_CODE='fake-access')
    def test_transport_sanitizes_ean_without_mutating_input(self):
        with patch('EcommerceApp.mungos_client.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            client = MungosClient()
            for operation, source in (('create', create_example()), ('update', deepcopy(EXPECTED_PUT))):
                with self.subTest(operation=operation):
                    source['ean'] = '4006381333932'
                    original = deepcopy(source)
                    method = session.post if operation == 'create' else session.put
                    response = method.return_value.__enter__.return_value
                    response.status_code = 200
                    response.iter_content.return_value = [b'{}']
                    if operation == 'create':
                        client.send_product(source)
                    else:
                        client.update_product('a9241b59-9e45-4840-9730-c93cb8ad9517', source)
                    self.assertEqual(method.call_args.kwargs['json']['ean'], '')
                    self.assertEqual(source, original)


@override_settings(SITE_URL='https://example.com', MUNGOS_ENABLED=False)
class MungosFinalDryRunTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            naziv='MT14007 MATE M8 CARP REEL 8000 FD', sifra='7889', barkod='14587589654',
            opis='', cijena=Decimal('138.25'), stanje=43, aktivan=True, na_stanju=True,
            kategorija=Category.objects.create(naziv='Mašinice'),
        )

    def test_create_and_update_dry_run_are_final_read_only_and_no_http(self):
        original = Product.objects.filter(pk=self.product.pk).values().get()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')) as http:
            for operation, fields in (('create', CREATE_FIELDS), ('update', UPDATE_FIELDS)):
                with self.subTest(operation=operation):
                    output = StringIO()
                    with CaptureQueriesContext(connection) as queries:
                        call_command('mungos_product_dry_run', self.product.pk, operation=operation, stdout=output)
                    self.assertTrue(queries)
                    self.assertTrue(all(row['sql'].lstrip().upper().startswith('SELECT') for row in queries))
                    result = json.loads(output.getvalue())
                    self.assertEqual(result['status'], 'READY_FOR_REVIEW')
                    self.assertEqual(result['payload']['ean'], '')
                    self.assertEqual(result['payload']['price'], 138.25)
                    self.assertEqual(set(result['payload']), set(fields))
                    expected = {**EXPECTED_PUT, 'price': 138.25, 'images': [],
                                'shortDescription': '', 'details': '', 'ean': ''}
                    if operation == 'create':
                        del expected['brandCode']
                        expected.update(HasVariants=False, Variants=[])
                    self.assertEqual(result['payload'], expected)
            http.assert_not_called()
        self.assertEqual(Product.objects.filter(pk=self.product.pk).values().get(), original)

    def test_invalid_uuid_dry_run_blocks_before_database(self):
        with patch('requests.sessions.Session.request') as http, CaptureQueriesContext(connection) as queries:
            with self.assertRaisesMessage(CommandError, 'NOT_SENT'):
                call_command('mungos_product_dry_run', self.product.pk, operation='update', mungos_uuid='../bad')
        self.assertEqual(len(queries), 0)
        http.assert_not_called()

    def test_variant_dry_run_and_confirmed_commands_block_no_http(self):
        ProductVariation.objects.create(artikal=self.product, naziv='Variant', sifra='V-1', stanje=1)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')) as http:
            for operation in ('create', 'update'):
                output = StringIO()
                call_command('mungos_product_dry_run', self.product.pk, operation=operation, stdout=output)
                self.assertEqual(json.loads(output.getvalue())['status'], 'NEEDS_REVIEW')
            with self.assertRaisesMessage(CommandError, 'NOT_SENT'):
                call_command('mungos_product_send', self.product.pk, confirm=True)
            with self.assertRaisesMessage(CommandError, 'NOT_SENT'):
                call_command('mungos_product_update', self.product.pk,
                             'a9241b59-9e45-4840-9730-c93cb8ad9517', confirm=True)
            http.assert_not_called()

    def test_gallery_only_gets_one_main_image_without_model_changes(self):
        ProductImage.objects.create(product=self.product, slika='first.jpg')
        ProductImage.objects.create(product=self.product, slika='second.jpg')
        before = list(self.product.dodatne_slike.values())
        preview = build_product_preview(self.product)
        self.assertEqual([row['isMainImage'] for row in preview['images']], [True, False])
        self.assertEqual(list(self.product.dodatne_slike.values()), before)

    def test_builder_none_price_and_description_are_diagnostic_without_writes(self):
        self.product.opis = None
        with patch.object(Product, 'prikazna_cijena', new_callable=PropertyMock,
                          return_value=None):
            preview = build_product_preview(self.product)
        self.assertEqual(preview['status'], 'NEEDS_REVIEW')
        self.assertIsNone(preview['payload']['price'])
        self.assertEqual(preview['payload']['details'], '')
        self.assertIsNone(self.product.opis)
        json.loads(sanitized_json(preview))
