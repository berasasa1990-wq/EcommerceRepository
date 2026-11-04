"""Retire only LiveVisitor's history triggers, retaining all existing data."""
import hashlib

from django.db import migrations


def remove_livevisitor_triggers(apps, schema_editor):
    connection = schema_editor.connection
    table = apps.get_model('EcommerceApp', 'LiveVisitor')._meta.db_table
    quote = connection.ops.quote_name
    trigger = 'retain_' + hashlib.sha256(table.encode()).hexdigest()[:16]
    with connection.cursor() as cursor:
        if table not in connection.introspection.table_names(cursor):
            return
        if connection.vendor == 'postgresql':
            cursor.execute('SHOW lock_timeout')
            previous_timeout = cursor.fetchone()[0]
            cursor.execute("SELECT set_config('lock_timeout', '5s', true)")
            cursor.execute(f'DROP TRIGGER IF EXISTS {quote(trigger)} ON {quote(table)}')
            cursor.execute("SELECT set_config('lock_timeout', %s, true)", [previous_timeout])
        elif connection.vendor == 'sqlite':
            for operation in ('insert', 'update', 'delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {quote(trigger + "_" + operation)}')


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0348_desktop_banner_videos')]
    operations = [migrations.RunPython(remove_livevisitor_triggers, migrations.RunPython.noop)]
