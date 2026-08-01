"""Enable pg_trgm (TrigramExtension) + GIN trigram indexes for fuzzy search.

TrigramExtension is a no-op on non-PostgreSQL backends.
GIN indexes are created only when vendor == postgresql.
"""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


def _is_postgres(schema_editor):
    return schema_editor.connection.vendor == 'postgresql'


def create_trgm_indexes(apps, schema_editor):
    if not _is_postgres(schema_editor):
        return
    statements = [
        """
        CREATE INDEX IF NOT EXISTS product_naziv_norm_trgm_idx
        ON "EcommerceApp_product"
        USING gin (naziv_normalized gin_trgm_ops)
        """,
        """
        CREATE INDEX IF NOT EXISTS product_naziv_trgm_idx
        ON "EcommerceApp_product"
        USING gin (naziv gin_trgm_ops)
        """,
        """
        CREATE INDEX IF NOT EXISTS brand_naziv_trgm_idx
        ON "EcommerceApp_brand"
        USING gin (naziv gin_trgm_ops)
        """,
        """
        CREATE INDEX IF NOT EXISTS category_naziv_trgm_idx
        ON "EcommerceApp_category"
        USING gin (naziv gin_trgm_ops)
        """,
        """
        CREATE INDEX IF NOT EXISTS tag_naziv_trgm_idx
        ON "EcommerceApp_tag"
        USING gin (naziv gin_trgm_ops)
        """,
        """
        CREATE INDEX IF NOT EXISTS searchsynonym_norm_trgm_idx
        ON "EcommerceApp_searchsynonym"
        USING gin (normalizovani_pojam gin_trgm_ops)
        """,
    ]
    with schema_editor.connection.cursor() as cursor:
        for sql in statements:
            cursor.execute(sql)


def drop_trgm_indexes(apps, schema_editor):
    if not _is_postgres(schema_editor):
        return
    names = [
        'product_naziv_norm_trgm_idx',
        'product_naziv_trgm_idx',
        'brand_naziv_trgm_idx',
        'category_naziv_trgm_idx',
        'tag_naziv_trgm_idx',
        'searchsynonym_norm_trgm_idx',
    ]
    with schema_editor.connection.cursor() as cursor:
        for name in names:
            cursor.execute(f'DROP INDEX IF EXISTS {name}')


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0174_product_attribute_measures'),
    ]

    operations = [
        # Official Django helper — CREATE EXTENSION IF NOT EXISTS pg_trgm
        TrigramExtension(),
        migrations.RunPython(create_trgm_indexes, drop_trgm_indexes),
    ]
