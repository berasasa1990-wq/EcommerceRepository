"""
PostgreSQL Full Text Search + pg_trgm setup.

- search_document (svi DB-evi)
- search_vector tsvector + GIN + pg_trgm GIN (samo PostgreSQL)
"""
from django.db import migrations, models


def setup_postgres_search(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        # tsvector kolona za weighted FTS
        cursor.execute(
            """
            ALTER TABLE "EcommerceApp_product"
            ADD COLUMN IF NOT EXISTS search_vector tsvector
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS product_search_vector_gin
            ON "EcommerceApp_product" USING GIN (search_vector)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS product_search_document_trgm
            ON "EcommerceApp_product" USING GIN (search_document gin_trgm_ops)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS product_naziv_trgm
            ON "EcommerceApp_product" USING GIN (naziv gin_trgm_ops)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS product_sifra_trgm
            ON "EcommerceApp_product" USING GIN (sifra gin_trgm_ops)
            """
        )


def teardown_postgres_search(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('DROP INDEX IF EXISTS product_search_vector_gin')
        cursor.execute('DROP INDEX IF EXISTS product_search_document_trgm')
        cursor.execute('DROP INDEX IF EXISTS product_naziv_trgm')
        cursor.execute('DROP INDEX IF EXISTS product_sifra_trgm')
        cursor.execute(
            'ALTER TABLE "EcommerceApp_product" DROP COLUMN IF EXISTS search_vector'
        )


def rebuild_search_index(apps, schema_editor):
    """Best-effort reindex nakon migracije (ne ruši migrate ako nešto zakaže)."""
    if schema_editor.connection.vendor != 'postgresql':
        # Na SQLite samo popuni search_document ako je prazan — kasnije signal
        return
    try:
        from EcommerceApp.product_search import rebuild_all_product_search_indexes
        rebuild_all_product_search_indexes()
    except Exception:
        # Indeks se može napuniti s: python manage.py rebuild_product_search
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0165_alter_category_search_tagovi'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='search_document',
            field=models.TextField(
                blank=True,
                default='',
                editable=False,
                help_text='Automatski: šifra, naziv, brend, kategorija, tagovi, opis, šifre varijacija.',
                verbose_name='Search document',
            ),
        ),
        migrations.RunPython(setup_postgres_search, teardown_postgres_search),
        migrations.RunPython(rebuild_search_index, migrations.RunPython.noop),
    ]
