import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from datetime import timedelta

from .models import Category, Product, ProductImage, ProductVariation, WarehouseLocation, WarehouseStock
from .mungos_product import build_product_preview, category_code


@override_settings(SITE_URL='https://carpologijabh.ba', MUNGOS_ENABLED=False)
class MungosProductTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(naziv='Štapovi')
        self.product = Product.objects.create(
            naziv='Test štap', sifra='ROD-1', opis='Opis', cijena=Decimal('100.00'),
            stanje=8, na_stanju=True, aktivan=True, kategorija=self.category,
        )

    def preview(self):
        return build_product_preview(self.product)

    def test_normal_product_read_only(self):
        with CaptureQueriesContext(connection) as queries:
            result = self.preview()
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))
        self.assertEqual(result['status'], 'READY_FOR_REVIEW')
        payload = result['payload']
        self.assertEqual((payload['id'], payload['sku']), ('ROD-1', 'ROD-1'))
        self.assertEqual((payload['price'], payload['quantityRemaining']), (100, 8))
        self.assertEqual(payload['currencyIsoCode'], 'BAM')
        self.assertEqual(payload['condition'], 'New')
        self.assertNotIn('condidtion', payload)
        self.assertFalse(payload['HasVariants'])

    def test_sale_uses_existing_price_and_expiry(self):
        self.product.akcijska_cijena = Decimal('75.00')
        self.assertEqual(self.preview()['payload']['price'], float(self.product.prikazna_cijena))
        self.assertEqual(self.preview()['price'], 75)
        self.product.akcija_do = timezone.localdate() - timedelta(days=1)
        self.assertEqual(self.preview()['price'], 100)
        with patch.object(Product, 'flash_sale_price', return_value=Decimal('60')):
            self.assertEqual(self.preview()['price'], 60)

    def test_create_ean_sanitization_changes_only_outbound_ean(self):
        baseline = self.preview()['payload']
        for value, expected in (
            ('12345670', '12345670'), ('1234567890128', '1234567890128'),
            ('12345671', ''), ('1234567890123', ''), ('14587589654', ''), ('', ''), (None, ''), ('abcdefgh', ''),
            ('1234567a', ''), (' 12345670', ''), ('１２３４５６７０', ''),
        ):
            with self.subTest(ean=value):
                self.product.barkod = value
                self.assertEqual(self.preview()['payload'], {**baseline, 'ean': expected})
                self.assertEqual(self.product.barkod, value)

    def test_invalid_ean_stays_in_database_after_create_and_update_builders(self):
        from .mungos_update import build_mungos_update_payload

        # Isolated test fixture; never rely on local Product 4455 matching production.
        Product.objects.filter(pk=self.product.pk).update(barkod='4006381333932')
        self.product.refresh_from_db()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')) as http:
            with CaptureQueriesContext(connection) as queries:
                preview = self.preview()
                update = build_mungos_update_payload(preview)
        http.assert_not_called()
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))
        self.assertEqual(preview['payload']['ean'], '')
        self.assertEqual(update['payload']['ean'], '')
        self.assertEqual(self.product.barkod, '4006381333932')
        self.product.refresh_from_db()
        self.assertEqual(self.product.barkod, '4006381333932')

    def test_without_stock_and_hidden(self):
        self.product.stanje = 0
        self.assertEqual(self.preview()['quantity'], 0)
        self.product.stanje = 8
        self.product.na_stanju = False
        self.assertEqual(self.preview()['quantity'], 0)
        self.product.na_stanju = True
        self.product.aktivan = False
        self.assertEqual(self.preview()['quantity'], 0)
        self.assertEqual(self.preview()['status'], 'NEEDS_REVIEW')

    def test_warehouse_matches_cart_after_reservations(self):
        location = WarehouseLocation.objects.create(sifra='MUNGOS-TEST', naziv='Magacin')
        row = WarehouseStock.objects.create(product=self.product, location=location, kolicina=7, rezervisano=3)
        self.assertEqual(self.preview()['quantity'], 4)
        row.refresh_from_db()
        self.assertEqual((row.kolicina, row.rezervisano), (7, 3))
        location.aktivan = False
        location.save()
        self.assertEqual(self.preview()['quantity'], 0)

    def test_main_and_extra_images(self):
        self.product.slika = 'products/main.jpg'
        ProductImage.objects.create(product=self.product, slika='products/extra.jpg')
        images = self.preview()['images']
        self.assertEqual(len(images), 2)
        self.assertEqual([image['isMainImage'] for image in images], [True, False])
        self.assertTrue(all(image['imageUrl'].startswith('https://') for image in images))
        self.assertTrue(images[0]['imageUrl'].endswith('/products/main.jpg'))

    def test_unknown_category_and_exact_ancestor_mapping(self):
        self.product.kategorija = Category.objects.create(naziv='Nepoznato')
        self.assertIsNone(self.preview()['categoryCode'])
        self.assertEqual(self.preview()['status'], 'NEEDS_REVIEW')
        self.product.kategorija.roditelj = self.category
        self.assertEqual(category_code(self.product.kategorija), category_code(self.category))
        self.product.kategorija = None
        self.assertEqual(self.preview()['status'], 'NEEDS_REVIEW')

    def test_variants_keep_sku_price_stock_image_and_require_review(self):
        variant = ProductVariation.objects.create(
            artikal=self.product, naziv='Crvena', sifra='RED-1', cijena=25,
            stanje=4, slika='products/variations/red.jpg',
        )
        result = self.preview()
        self.assertEqual(result['status'], 'NEEDS_REVIEW')
        self.assertEqual(result['variantCount'], 1)
        self.assertTrue(result['payload']['HasVariants'])
        row = result['payload']['Variants'][0]
        self.assertEqual((row['sku'], row['price'], row['sellingPrice'], row['quantityRemaining']), ('RED-1', 25, 25, 4))
        self.assertEqual(row['attributes'], [])
        self.assertTrue(row['images'][0]['imageUrl'].endswith('/red.jpg'))
        self.product.akcijska_cijena = Decimal('80')
        Product.objects.filter(pk=self.product.pk).update(akcijska_cijena=Decimal('80'))
        variant.artikal = self.product
        with patch.object(Product, 'flash_sale_price', return_value=None):
            self.assertEqual(float(variant.prikazna_cijena), 20)
            self.assertEqual(self.preview()['payload']['Variants'][0]['price'], 20)
        self.assertEqual(result['quantity'], 8)  # No variant double-counting.

    @override_settings(MUNGOS_API_KEY='secret-abc', MUNGOS_ECOMMERCE_ACCESS_CODE='access-xyz')
    def test_command_no_network_no_writes_and_sanitizes_catalog_text(self):
        Product.objects.filter(pk=self.product.pk).update(opis='secret-abc access-xyz')
        output = StringIO()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')) as http:
            with CaptureQueriesContext(connection) as queries:
                call_command('mungos_product_dry_run', self.product.pk, stdout=output)
        http.assert_not_called()
        self.assertTrue(all(query['sql'].lstrip().upper().startswith('SELECT') for query in queries))
        self.assertNotIn('secret-abc', output.getvalue())
        self.assertNotIn('access-xyz', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['payload']['details'], '[REDACTED] [REDACTED]')

    def test_missing_sku_and_missing_product(self):
        self.product.sifra = None
        self.assertEqual(self.preview()['status'], 'NEEDS_REVIEW')
        self.assertEqual(self.preview()['payload']['id'], '')
        with self.assertRaisesMessage(CommandError, 'Proizvod ne postoji'):
            call_command('mungos_product_dry_run', 999999999)

    def test_variant_warehouse_scope_and_synced_empty_stock(self):
        variant = ProductVariation.objects.create(artikal=self.product, naziv='V', sifra='V-1', stanje=99)
        location = WarehouseLocation.objects.create(sifra='MUNGOS-V', naziv='Magacin')
        WarehouseStock.objects.create(product=self.product, variation=variant, location=location, kolicina=6, rezervisano=2)
        result = self.preview()
        self.assertEqual(result['quantity'], 0)
        self.assertEqual(result['payload']['Variants'][0]['quantityRemaining'], 4)
        WarehouseStock.objects.all().delete()
        self.product.magacin_sync_at = timezone.now()
        self.assertEqual(self.preview()['quantity'], 0)

    def test_unsafe_image_url_is_omitted_and_requires_review(self):
        self.product.slika = 'products/main.jpg'
        with patch.object(self.product.slika.storage, 'url', return_value='https://images.example/photo.jpg?token=private'):
            result = self.preview()
        self.assertEqual(result['images'], [])
        self.assertEqual(result['status'], 'NEEDS_REVIEW')
