import json
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from .models import Category, Product
from .mungos_categories import category_code, CATEGORY_PREFIX, EXPLICIT_CATEGORY_MAPPING
from .mungos_product import build_product_preview


class CategoryMappingTests(TestCase):
    def test_explicit_parent_and_override(self):
        parent = Category.objects.create(naziv='Feeder stapovi')
        child = Category.objects.create(naziv='Nova podkategorija', roditelj=parent)
        self.assertEqual(category_code(child), CATEGORY_PREFIX + 'FishingRods')
        child.naziv = 'Varalice more TEST'
        self.assertEqual(category_code(child), CATEGORY_PREFIX + 'Lures')
        child.naziv = 'Nepoznata kategorija'
        with patch.dict(EXPLICIT_CATEGORY_MAPPING, {'nepoznata kategorija': None}):
            self.assertIsNone(category_code(child))

    def test_no_fuzzy_and_cycle(self):
        category = Category.objects.create(naziv='Možda štapovi nešto')
        self.assertIsNone(category_code(category))
        category.roditelj = category
        self.assertIsNone(category_code(category))

    def test_audit_no_http_or_writes(self):
        mapped = Category.objects.create(naziv='Udice za ribolov')
        unknown = Category.objects.create(naziv='Nepoznato')
        for category, sku in [(mapped, 'MAPPED'), (unknown, 'UNKNOWN'), (None, 'NO-CATEGORY')]:
            Product.objects.create(naziv=sku, sifra=sku, cijena=10, kategorija=category, aktivan=True)
        out = StringIO()
        with patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')) as http:
            with CaptureQueriesContext(connection) as queries:
                call_command('mungos_category_audit', stdout=out)
        http.assert_not_called()
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))
        data = json.loads(out.getvalue())
        self.assertEqual(data['TOTAL_CATEGORIES'], 2)
        self.assertEqual(data['MAPPED_CATEGORIES'], 1)
        self.assertEqual(data['unmapped_categories'][0]['name'], 'Nepoznato')

        self.assertEqual(data['PRODUCTS_READY_BY_CATEGORY'], 1)
        self.assertEqual(data['PRODUCTS_BLOCKED_BY_CATEGORY'], 2)
        self.assertEqual(data['PRODUCTS_WITHOUT_CATEGORY'], 1)

    def test_unknown_and_absent_category_require_review(self):
        unknown = Category.objects.create(naziv='Nepoznata oprema')
        for index, category in enumerate([unknown, None]):
            product = Product.objects.create(
                naziv='Test', sifra=f'TEST-{index}', cijena=10, kategorija=category, aktivan=True,
            )
            preview = build_product_preview(product)
            self.assertIsNone(preview['categoryCode'])
            self.assertEqual(preview['status'], 'NEEDS_REVIEW')

    def test_confirmed_root_passes_payload_validation(self):
        root = Category.objects.create(naziv='Oprema za ribolov')
        product = Product.objects.create(
            naziv='Test', sifra='ROOT', cijena=10, kategorija=root, aktivan=True,
        )
        preview = build_product_preview(product)
        self.assertEqual(preview['categoryCode'], CATEGORY_PREFIX.rstrip('_'))
        self.assertEqual(preview['status'], 'READY_FOR_REVIEW')
