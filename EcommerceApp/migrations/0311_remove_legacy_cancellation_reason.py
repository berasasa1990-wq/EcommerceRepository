from django.db import migrations


def remove_legacy_reason(apps, schema_editor):
    connection = schema_editor.connection
    table = 'EcommerceApp_order'
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(cursor, table)
        if not any(column.name == 'razlog_otkazivanja' for column in columns):
            return
        if connection.vendor == 'sqlite':
            cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=%s", [table])
            triggers = [row[0] for row in cursor.fetchall()]
            for trigger in triggers:
                cursor.execute('DROP TRIGGER ' + connection.ops.quote_name(trigger))
        cursor.execute('ALTER TABLE ' + connection.ops.quote_name(table) + ' DROP COLUMN ' + connection.ops.quote_name('razlog_otkazivanja'))
    from EcommerceApp.retention_triggers import install_history
    install_history(connection)


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0310_scratch_reward_coupons')]
    operations = [migrations.RunPython(remove_legacy_reason)]
