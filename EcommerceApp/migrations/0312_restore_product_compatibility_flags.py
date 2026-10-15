from django.db import migrations, models


def ensure_flags(apps, schema_editor):
    product = apps.get_model('EcommerceApp', 'Product')
    connection = schema_editor.connection
    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        present = {column.name for column in connection.introspection.get_table_description(cursor, product._meta.db_table)}
        for name, default in [('pracenje_zaliha', True), ('modul_sakriven', False), ('enabled_360', False)]:
            if name in present:
                continue
            field = models.BooleanField(default=default)
            field.set_attributes_from_name(name)
            definition, parameters = schema_editor.column_sql(product, field, include_default=True)
            cursor.execute(f'ALTER TABLE {quote(product._meta.db_table)} ADD COLUMN {quote(name)} {definition}', parameters)


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0311_remove_legacy_cancellation_reason')]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunPython(ensure_flags)],
        state_operations=[
            migrations.AddField(model_name='product', name='pracenje_zaliha', field=models.BooleanField(default=True)),
            migrations.AddField(model_name='product', name='modul_sakriven', field=models.BooleanField(default=False)),
            migrations.AddField(model_name='product', name='enabled_360', field=models.BooleanField(default=False)),
        ],
    )]
