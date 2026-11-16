"""Canonical search expressions stay current even for bulk/SQL catalogue imports."""
from django.db import migrations


FUNCTIONS = """
CREATE OR REPLACE FUNCTION public.shop_search_fold(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
 SELECT translate(replace(replace(lower(normalize(coalesce($1, ''), NFC)), 'đ', 'dj'), 'Đ', 'dj'),
                  'šŠčČćĆžŽ', 'sscccczz')
$$;
CREATE OR REPLACE FUNCTION public.shop_search_compact(text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
 SELECT regexp_replace(public.shop_search_fold($1), '[^a-z0-9]', '', 'g')
$$;
"""
INDEXES = [
    ('shop_name_fold_trgm', 'EcommerceApp_product', 'gin (public.shop_search_fold(naziv) gin_trgm_ops)'),
    ('shop_sku_compact_btree', 'EcommerceApp_product', 'btree (public.shop_search_compact(sifra))'),
    ('shop_sku_compact_trgm', 'EcommerceApp_product', 'gin (public.shop_search_compact(sifra) gin_trgm_ops)'),
    ('shop_var_sku_compact_btree', 'EcommerceApp_productvariation', 'btree (public.shop_search_compact(sifra))'),
    ('shop_var_sku_compact_trgm', 'EcommerceApp_productvariation', 'gin (public.shop_search_compact(sifra) gin_trgm_ops)'),
]


def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        cursor.execute(FUNCTIONS)
        for name, table, definition in INDEXES:
            # Failed concurrent builds leave an INVALID index. IF NOT EXISTS
            # alone would silently keep it and never rebuild it on retry.
            cursor.execute(
                "SELECT n.nspname FROM pg_catalog.pg_index i "
                "JOIN pg_catalog.pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relname = %s AND i.indrelid = pg_catalog.to_regclass(%s) "
                "AND NOT i.indisvalid",
                [name, f'"{table}"'],
            )
            invalid = cursor.fetchone()
            if invalid:
                qualified = f'{schema_editor.quote_name(invalid[0])}.{schema_editor.quote_name(name)}'
                cursor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {qualified}')
            cursor.execute(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON "{table}" USING {definition}')


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for name, _, _ in reversed(INDEXES):
            cursor.execute(f'DROP INDEX CONCURRENTLY IF EXISTS {name}')
        cursor.execute('DROP FUNCTION IF EXISTS public.shop_search_compact(text)')
        cursor.execute('DROP FUNCTION IF EXISTS public.shop_search_fold(text)')


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('EcommerceApp', '0352_akcija_plus_artikli_alter_akcija_tip')]
    operations = [migrations.RunPython(install, uninstall)]
