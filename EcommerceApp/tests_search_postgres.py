"""Regression for PostgreSQL's restricted search_path during index creation."""
from importlib import import_module
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, transaction, DatabaseError
from django.test import SimpleTestCase, TransactionTestCase

from .models import Product
from .search_expressions import SearchCompact, SearchFold


class SearchFunctionCompilationTests(SimpleTestCase):
    def test_postgresql_qualifies_both_function_calls(self):
        qs = Product.objects.annotate(
            search_code=SearchCompact('sifra'), search_name=SearchFold('naziv'),
        ).values('search_code', 'search_name')
        # Compile only: this test does not require a PostgreSQL server.
        with patch.object(connection, 'vendor', 'postgresql'):
            sql, _ = qs.query.sql_with_params()
        self.assertIn('public.shop_search_compact(', sql)
        self.assertIn('public.shop_search_fold(', sql)


@skipUnless(connection.vendor == 'postgresql', 'Requires a PostgreSQL server')
class SearchPostgresIndexTests(TransactionTestCase):
    index_name = 'test_shop_search_compact_retry'

    def setUp(self):
        self.product = Product.objects.create(naziv='Test čizme', sifra='MT-12221', cijena=10)
        self.migration = import_module('EcommerceApp.migrations.0353_search_expression_indexes')

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS public.{self.index_name}')
        super().tearDown()

    def test_compact_with_restricted_search_path(self):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SET LOCAL search_path TO pg_catalog, pg_temp')
            cursor.execute("SELECT public.shop_search_compact('MT-12221'), public.shop_search_fold('Mašinica Đak')")
            self.assertEqual(cursor.fetchone(), ('mt12221', 'masinica djak'))

    def test_concurrent_index_build_can_inline_compact_function(self):
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE INDEX CONCURRENTLY {self.index_name} ON "EcommerceApp_product" (public.shop_search_compact(sifra))')
            cursor.execute('SELECT indisvalid FROM pg_catalog.pg_index WHERE indexrelid = %s::regclass', [self.index_name])
            self.assertTrue(cursor.fetchone()[0])

    def test_failed_concurrent_index_is_rebuilt_on_retry(self):
        with connection.cursor() as cursor:
            with self.assertRaises(DatabaseError):
                cursor.execute(f'CREATE INDEX CONCURRENTLY {self.index_name} ON "EcommerceApp_product" ((1 / (length(sifra) - length(sifra))))')
            cursor.execute('SELECT indisvalid FROM pg_catalog.pg_index WHERE indexrelid = %s::regclass', [self.index_name])
            self.assertFalse(cursor.fetchone()[0])
        definitions = [(self.index_name, 'EcommerceApp_product', 'btree (public.shop_search_compact(sifra))')]
        with patch.object(self.migration, 'INDEXES', definitions), connection.schema_editor(atomic=False) as editor:
            self.migration.install(None, editor)
        with connection.cursor() as cursor:
            cursor.execute('SELECT indisvalid FROM pg_catalog.pg_index WHERE indexrelid = %s::regclass', [self.index_name])
            self.assertTrue(cursor.fetchone()[0])
