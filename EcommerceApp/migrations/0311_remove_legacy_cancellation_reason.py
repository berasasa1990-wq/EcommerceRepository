import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import migrations


def remove_legacy_reason(apps, schema_editor):
    connection = schema_editor.connection
    table = 'EcommerceApp_order'
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(cursor, table)
        if not any(column.name == 'razlog_otkazivanja' for column in columns):
            return  # Also safe when the database column was already removed.

        # Keep the legacy values in permanent history before removing their column.
        revision = apps.get_model('EcommerceApp', 'SystemDataRevision')
        cursor.execute(f'SELECT * FROM {quote(table)} '
                       f"WHERE COALESCE({quote('razlog_otkazivanja')}, '') <> ''")
        names = [column[0] for column in cursor.description]
        for values in cursor.fetchall():
            snapshot = json.loads(json.dumps(dict(zip(names, values)), cls=DjangoJSONEncoder))
            revision.objects.using(connection.alias).create(
                table_name=table, record_key=str(snapshot['id']), operation='BASELINE',
                before=None, after=snapshot,
            )

        if connection.vendor == 'sqlite':
            # SQLite embeds column names in the three history trigger definitions.
            # Preserve unrelated triggers; rebuild only this table's audit triggers.
            digest = hashlib.sha256(table.encode()).hexdigest()[:16]
            for operation in ('insert', 'update', 'delete'):
                cursor.execute('DROP TRIGGER IF EXISTS ' + quote('retain_' + digest + '_' + operation))
        cursor.execute(f'ALTER TABLE {quote(table)} DROP COLUMN {quote("razlog_otkazivanja")}')

    if connection.vendor == 'sqlite':
        from EcommerceApp.retention_triggers import install_history
        install_history(connection, tables=[table])
    # PostgreSQL reads NEW/OLD dynamically using to_jsonb; existing triggers remain valid.
    # Never rebuild all tables' triggers while holding the order table's DDL lock.


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0310_scratch_reward_coupons')]
    operations = [migrations.RunPython(remove_legacy_reason)]
