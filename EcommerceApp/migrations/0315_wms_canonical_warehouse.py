from django.db import migrations, models
import django.db.models.deletion


def map_locations(apps, schema_editor):
    alias = schema_editor.connection.alias
    Location = apps.get_model('EcommerceApp', 'WMSLocation')
    mapping = dict(Location.objects.using(alias).exclude(legacy_location_id=None).values_list('pk', 'legacy_location_id'))
    specs = [('WMSPickLine', [('archived_location_id', 'location_id')]),
             ('WMSTransfer', [('archived_source_id', 'source_id'), ('archived_destination_id', 'destination_id')]),
             ('WMSSettings', [('archived_transfer_location_id', 'transfer_location_id'), ('archived_picking_last_location_id', 'picking_last_location_id')])]
    unresolved = []
    for name, fields in specs:
        Model = apps.get_model('EcommerceApp', name)
        for row in Model.objects.using(alias).all().iterator():
            updates = {}
            for old, new in fields:
                old_id = getattr(row, old)
                if old_id is not None and old_id not in mapping:
                    unresolved.append(f'{name}:{row.pk}:{old_id}')
                if old_id in mapping:
                    updates[new] = mapping[old_id]
            if updates:
                Model.objects.using(alias).filter(pk=row.pk).update(**updates)
    if unresolved:
        raise RuntimeError('WMS lokacije nemaju vezu s originalnim magacinom. Prvo ih povežite; prijelaz je poništen. Zapisi: ' + ', '.join(unresolved[:20]))


def rename_retained_fk_index(schema_editor, model, old_column, new_column):
    """PostgreSQL keeps an implicit FK index name when its column is renamed."""
    connection = schema_editor.connection
    if connection.vendor != 'postgresql':
        return
    table = model._meta.db_table
    old_name = schema_editor._create_index_name(table, [old_column], suffix='')
    new_name = schema_editor._create_index_name(table, [new_column], suffix='')
    with connection.cursor() as cursor:
        indexes = connection.introspection.get_constraints(cursor, table)
        retained = indexes.get(old_name)
        if retained is None:
            # A manually named index does not occupy the new field's default name.
            return
        if not (retained.get('index') and not retained.get('unique')
                and not retained.get('primary_key')
                and retained.get('columns') == [new_column]):
            raise RuntimeError(f'Unsafe WMS index rename: {table}.{old_name} is not the expected index on {new_column}.')
        cursor.execute(
            'SELECT n.nspname, EXISTS (SELECT 1 FROM pg_class i '
            'WHERE i.relname = %s AND i.relnamespace = t.relnamespace) '
            'FROM pg_class t JOIN pg_namespace n ON n.oid = t.relnamespace '
            'WHERE t.oid = %s::regclass',
            [new_name, schema_editor.quote_name(table)],
        )
        namespace, target_exists = cursor.fetchone()
        if target_exists:
            raise RuntimeError(f'Unsafe WMS index rename: target relation {new_name} already exists.')
    quote = schema_editor.quote_name
    schema_editor.execute(
        f'ALTER INDEX {quote(namespace)}.{quote(old_name)} RENAME TO {quote(new_name)}'
    )


class RenameFKWithRetainedIndex(migrations.RenameField):
    """Preserve RenameField state and data, while freeing its reused index name."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        super().database_forwards(app_label, schema_editor, from_state, to_state)
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            old_model = from_state.apps.get_model(app_label, self.model_name)
            rename_retained_fk_index(schema_editor, model,
                old_model._meta.get_field(self.old_name).column,
                model._meta.get_field(self.new_name).column)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        super().database_backwards(app_label, schema_editor, from_state, to_state)
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model):
            old_model = from_state.apps.get_model(app_label, self.model_name)
            rename_retained_fk_index(schema_editor, model,
                old_model._meta.get_field(self.new_name).column,
                model._meta.get_field(self.old_name).column)


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0314_bera_appearance')]
    operations = [
        migrations.AddField(model_name='wmsorderitem', name='varijacija', field=models.ForeignKey(to='EcommerceApp.productvariation', on_delete=django.db.models.deletion.SET_NULL, related_name='+', null=True, blank=True)),
        migrations.RemoveConstraint(model_name='wmspickline', name='bera_wms_pick_item_location'),
        RenameFKWithRetainedIndex(model_name='wmspickline', old_name='location', new_name='archived_location'),
        RenameFKWithRetainedIndex(model_name='wmstransfer', old_name='source', new_name='archived_source'),
        RenameFKWithRetainedIndex(model_name='wmstransfer', old_name='destination', new_name='archived_destination'),
        RenameFKWithRetainedIndex(model_name='wmssettings', old_name='transfer_location', new_name='archived_transfer_location'),
        RenameFKWithRetainedIndex(model_name='wmssettings', old_name='picking_last_location', new_name='archived_picking_last_location'),
        migrations.AlterField(model_name='wmspickline', name='archived_location', field=models.ForeignKey(to='EcommerceApp.wmslocation', on_delete=django.db.models.deletion.PROTECT, null=True, blank=True)),
        migrations.AlterField(model_name='wmstransfer', name='archived_source', field=models.ForeignKey(to='EcommerceApp.wmslocation', on_delete=django.db.models.deletion.PROTECT, related_name='outgoing_transfers', null=True, blank=True)),
        migrations.AlterField(model_name='wmstransfer', name='archived_destination', field=models.ForeignKey(to='EcommerceApp.wmslocation', on_delete=django.db.models.deletion.PROTECT, related_name='incoming_transfers', null=True, blank=True)),
        migrations.AddField(model_name='wmspickline', name='location', field=models.ForeignKey(to='EcommerceApp.warehouselocation', on_delete=django.db.models.deletion.PROTECT, related_name='bera_pick_lines', null=True, blank=True)),
        migrations.AddField(model_name='wmstransfer', name='source', field=models.ForeignKey(to='EcommerceApp.warehouselocation', on_delete=django.db.models.deletion.PROTECT, related_name='bera_outgoing_transfers', null=True, blank=True)),
        migrations.AddField(model_name='wmstransfer', name='destination', field=models.ForeignKey(to='EcommerceApp.warehouselocation', on_delete=django.db.models.deletion.PROTECT, related_name='bera_incoming_transfers', null=True, blank=True)),
        migrations.AddField(model_name='wmstransfer', name='variation', field=models.ForeignKey(to='EcommerceApp.productvariation', on_delete=django.db.models.deletion.PROTECT, related_name='+', null=True, blank=True)),
        migrations.AddField(model_name='wmssettings', name='transfer_location', field=models.ForeignKey(to='EcommerceApp.warehouselocation', on_delete=django.db.models.deletion.SET_NULL, related_name='+', verbose_name='Lokacija za prenos', null=True, blank=True)),
        migrations.AddField(model_name='wmssettings', name='picking_last_location', field=models.ForeignKey(to='EcommerceApp.warehouselocation', on_delete=django.db.models.deletion.SET_NULL, related_name='+', verbose_name='Posljednja lokacija za odvajanje', null=True, blank=True)),
        migrations.RunPython(map_locations, migrations.RunPython.noop),
        migrations.AddConstraint(model_name='wmspickline', constraint=models.UniqueConstraint(fields=['order', 'item', 'location'], name='bera_wms_pick_item_location')),
    ]
