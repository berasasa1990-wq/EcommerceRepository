"""Inspect real Requests serialization at the final pre-network boundary."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Category, Product
from .mungos_client import MungosClient, MungosError
from .mungos_product import build_product_preview
from .mungos_update import build_mungos_update_payload

UUID = 'a9241b59-9e45-4840-9730-c93cb8ad9517'


class CapturedRequest(Exception):
    pass


@override_settings(MUNGOS_ENABLED=True, MUNGOS_ENVIRONMENT='staging',
    MUNGOS_BASE_URL='https://staging.mungos.ba/api/v1/connector',
    MUNGOS_API_KEY='test-key', MUNGOS_ECOMMERCE_ACCESS_CODE='test-access', SITE_URL='https://example.com')
class StandardPutSerializedTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(naziv='Sale reel', sifra='STD-PUT', cijena=Decimal('6.60'),
            akcijska_cijena=Decimal('5.28'), opis='Opis', stanje=5, na_stanju=True, aktivan=True,
            kategorija=Category.objects.create(naziv='Mašinice'))

    def capture(self):
        preview = build_mungos_update_payload(build_product_preview(self.product))
        self.assertFalse(preview['reviewReasons'])
        captured = {}
        def intercept(session, request, **kwargs):
            captured['method'] = request.method
            captured['url'] = request.url
            captured['body'] = json.loads(request.body)
            raise CapturedRequest()
        before = Product.objects.values().get(pk=self.product.pk)
        with patch('requests.sessions.Session.send', intercept), \
             patch('socket.socket.connect', side_effect=AssertionError('No network permitted')):
            with self.assertRaises(CapturedRequest):
                MungosClient().update_product(UUID, preview['payload'])
        self.assertEqual(Product.objects.values().get(pk=self.product.pk), before)
        self.assertEqual(captured['method'], 'PUT')
        self.assertEqual(captured['url'], 'https://staging.mungos.ba/api/v1/connector/standard/product/' + UUID)
        body = captured['body']
        self.assertNotIn('ProductPrice', body)
        self.assertTrue({'price', 'sellingPrice', 'discountEndDate', 'currencyIsoCode'} <= body.keys())
        self.assertFalse({'Price', 'SellingPrice', 'DiscountEndDate'} & body.keys())
        self.assertEqual(body['currencyIsoCode'], 'BAM')
        return body

    def test_sale_final_serialized_request_has_regular_and_selling_prices(self):
        body = self.capture()
        self.assertEqual(body['price'], 6.60)
        self.assertEqual(body['sellingPrice'], 5.28)
        self.assertIsNone(body['discountEndDate'])

    def test_normal_final_serialized_request_has_equal_prices(self):
        self.product.cijena = Decimal('20.00')
        self.product.akcijska_cijena = None
        self.product.save()
        body = self.capture()
        self.assertEqual(body['price'], 20.00)
        self.assertEqual(body['sellingPrice'], 20.00)
        self.assertIsNone(body['discountEndDate'])

    def test_discount_end_date_is_top_level_iso_utc_and_expired_sale_is_null(self):
        self.product.akcija_do = timezone.localdate() + timedelta(days=26)
        self.product.save()
        self.assertEqual(self.capture()['discountEndDate'], self.product.akcija_do.isoformat() + 'T23:59:59Z')
        self.product.akcija_do = timezone.localdate() - timedelta(days=1)
        self.product.save()
        body = self.capture()
        self.assertEqual(body['price'], body['sellingPrice'])
        self.assertIsNone(body['discountEndDate'])

    def test_standard_transport_rejects_nested_old_payload_before_http(self):
        source = build_product_preview(self.product)['payload']
        with patch('requests.sessions.Session.send') as send:
            with self.assertRaisesMessage(MungosError, 'NOT_SENT'):
                MungosClient().update_product(UUID, source)
        send.assert_not_called()
