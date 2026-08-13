from django.db import migrations, models


def fill_variation_normalized(apps, schema_editor):
    connection = schema_editor.connection
    table = 'EcommerceApp_productvariation'
    vendor = connection.vendor
    with connection.cursor() as cursor:
        if vendor == 'postgresql':
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s
                """,
                [table],
            )
            existing = {row[0] for row in cursor.fetchall()}
        else:
            cursor.execute(f'PRAGMA table_info("{table}")')
            existing = {row[1] for row in cursor.fetchall()}

        if 'naziv_normalized' not in existing:
            cursor.execute(
                f'ALTER TABLE "{table}" ADD COLUMN "naziv_normalized" varchar(120) DEFAULT \'\''
            )
        else:
            cursor.execute(
                f'UPDATE "{table}" SET "naziv_normalized" = COALESCE("naziv_normalized", \'\')'
            )
        if 'sifra_normalized' not in existing:
            cursor.execute(
                f'ALTER TABLE "{table}" ADD COLUMN "sifra_normalized" varchar(80) DEFAULT \'\''
            )
        else:
            cursor.execute(
                f'UPDATE "{table}" SET "sifra_normalized" = COALESCE("sifra_normalized", \'\')'
            )


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0185_product_magacin_sync_at'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='productvariation',
                    name='naziv_normalized',
                    field=models.CharField(
                        blank=True, db_index=True, default='', editable=False, max_length=120,
                    ),
                ),
                migrations.AddField(
                    model_name='productvariation',
                    name='sifra_normalized',
                    field=models.CharField(
                        blank=True, db_index=True, default='', editable=False, max_length=80,
                    ),
                ),
            ],
            database_operations=[],
        ),
        migrations.RunPython(fill_variation_normalized, migrations.RunPython.noop),
    ]
