"""
Osiguraj search_* kolone na Product s DEFAULT '' (NOT NULL).

Production je imao search_keywords NOT NULL bez defaulta → Odoo import:
  null value in column "search_keywords" violates not-null constraint
"""

from django.db import migrations, models


SEARCH_COLUMNS = {
    'search_keywords': "text DEFAULT '' NOT NULL",
    'naziv_normalized': "varchar(220) DEFAULT '' NOT NULL",
    'sifra_normalized': "varchar(80) DEFAULT '' NOT NULL",
    'barkod_normalized': "varchar(80) DEFAULT '' NOT NULL",
    'search_document': "text DEFAULT '' NOT NULL",
}


def ensure_search_columns(apps, schema_editor):
    connection = schema_editor.connection
    vendor = connection.vendor
    table = 'EcommerceApp_product'

    with connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                """,
                [table],
            )
            existing = {row[0] for row in cursor.fetchall()}

            for col, ddl in SEARCH_COLUMNS.items():
                if col not in existing:
                    cursor.execute(
                        f'ALTER TABLE "{table}" ADD COLUMN "{col}" {ddl}'
                    )
                else:
                    # Postojeća kolona: popuni NULL i postavi DEFAULT
                    cursor.execute(
                        f'UPDATE "{table}" SET "{col}" = \'\' '
                        f'WHERE "{col}" IS NULL'
                    )
                    cursor.execute(
                        f'ALTER TABLE "{table}" '
                        f'ALTER COLUMN "{col}" SET DEFAULT \'\''
                    )
                    cursor.execute(
                        f'ALTER TABLE "{table}" '
                        f'ALTER COLUMN "{col}" SET NOT NULL'
                    )
            return

        # SQLite
        cursor.execute(f'PRAGMA table_info("{table}")')
        existing = {row[1] for row in cursor.fetchall()}
        for col, ddl in SEARCH_COLUMNS.items():
            if col not in existing:
                if 'text' in ddl:
                    cursor.execute(
                        f'ALTER TABLE "{table}" ADD COLUMN "{col}" text DEFAULT \'\''
                    )
                else:
                    max_len = '220' if '220' in ddl else '80'
                    cursor.execute(
                        f'ALTER TABLE "{table}" ADD COLUMN "{col}" '
                        f'varchar({max_len}) DEFAULT \'\''
                    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0170_order_odoo_sale_order'),
    ]

    operations = [
        migrations.RunPython(ensure_search_columns, noop_reverse),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='product',
                    name='search_keywords',
                    field=models.TextField(
                        blank=True,
                        default='',
                        editable=False,
                        help_text='Interni blob za pretragu (opcionalno).',
                        verbose_name='Search keywords',
                    ),
                ),
                migrations.AddField(
                    model_name='product',
                    name='naziv_normalized',
                    field=models.CharField(
                        blank=True, db_index=True, default='', editable=False, max_length=220,
                    ),
                ),
                migrations.AddField(
                    model_name='product',
                    name='sifra_normalized',
                    field=models.CharField(
                        blank=True, db_index=True, default='', editable=False, max_length=80,
                    ),
                ),
                migrations.AddField(
                    model_name='product',
                    name='barkod_normalized',
                    field=models.CharField(
                        blank=True, db_index=True, default='', editable=False, max_length=80,
                    ),
                ),
                migrations.AddField(
                    model_name='product',
                    name='search_document',
                    field=models.TextField(blank=True, default='', editable=False),
                ),
            ],
            database_operations=[],  # already handled by RunPython
        ),
    ]
