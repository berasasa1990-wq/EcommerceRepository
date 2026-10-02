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

    def test_production_parents_and_inheritance(self):
        from .mungos_categories import PRODUCTION_CATEGORY_ID_MAPPING, resolve_category
        for pk, (name, code) in PRODUCTION_CATEGORY_ID_MAPPING.items():
            with self.subTest(pk=pk):
                parent = Category(pk=pk, naziv=name)
                child = Category(pk=1000 + pk, naziv='Nova podkategorija', roditelj=parent)
                self.assertEqual(resolve_category(parent), (code, pk))
                self.assertEqual(resolve_category(child), (code, pk))
                parent.naziv = 'Nepovezana kategorija'
                self.assertIsNone(category_code(child))

    def test_production_overrides_prevent_wrong_inheritance(self):
        from .mungos_categories import PRODUCTION_CATEGORY_OVERRIDES, resolve_category
        for pk, name in [(143, 'Udice i sitni pribor'), (173, 'Mamci'),
                         (181, 'Odjeća i obuća'), (167, 'Oprema')]:
            parent = Category(pk=pk, naziv=name)
            for index, (child_name, expected) in enumerate(PRODUCTION_CATEGORY_OVERRIDES.items()):
                with self.subTest(parent=pk, child=child_name):
                    child = Category(pk=2000 + index, naziv=child_name.upper(), roditelj=parent)
                    self.assertEqual(resolve_category(child), (expected, child.pk))

    def test_named_production_children_inherit(self):
        groups = [
            (143, 'Udice i sitni pribor', 'Hooks', [
                'Jednokrake Udice', 'Vezane Udice', 'Udice za Teži Ribolov',
                'Jig udice', 'Worm i Baitholder udice', 'Saranske udice']),
            (157, 'Feeder oprema', 'Feeders', [
                'Kavezne hranilice', 'Metod hranilice', 'Feeder Sitnice', 'Feeder dodaci']),
            (162, 'Kutije i torble', 'Accessories', [
                'Torbe za štapove', 'Torbe za pribor', 'Kutije za pribor', 'Kante za prihranu']),
            (167, 'Oprema', 'Accessories', [
                'Rod pod i držači', 'Meredovi i čuvarke', 'Spod stalci', 'Signalizatori',
                'Vage i griperi', 'Prostirke', 'Swingeri / Hengeri']),
            (173, 'Mamci', 'GroundbaitsBaits', [
                'Boila', 'Wafteri', 'Prihrana', 'Pva Materijali', 'Vještački mamci', 'Pelet']),
            (181, 'Odjeća i obuća', 'FishingWear', [
                'Jakne', 'Majice', 'Obuća', 'Pantalone', 'Rukavice', 'Termo odijela',
                'Kabanice', 'Kape i kacketi', 'Prsluci', 'Wadersi',
                'Majice i duksevi', 'Pantalone i sorcevi']),
        ]
        for pk, name, suffix, children in groups:
            parent = Category(pk=pk, naziv=name)
            for index, child_name in enumerate(children):
                with self.subTest(child=child_name):
                    child = Category(pk=3000 + index, naziv=child_name, roditelj=parent)
                    self.assertEqual(category_code(child), CATEGORY_PREFIX + suffix)
