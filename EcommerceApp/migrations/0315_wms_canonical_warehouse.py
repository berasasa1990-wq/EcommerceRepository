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


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0314_bera_appearance')]
    operations = [
        migrations.AddField(model_name='wmsorderitem', name='varijacija', field=models.ForeignKey(to='EcommerceApp.productvariation', on_delete=django.db.models.deletion.SET_NULL, related_name='+', null=True, blank=True)),
        migrations.RemoveConstraint(model_name='wmspickline', name='bera_wms_pick_item_location'),
        migrations.RenameField(model_name='wmspickline', old_name='location', new_name='archived_location'),
        migrations.RenameField(model_name='wmstransfer', old_name='source', new_name='archived_source'),
        migrations.RenameField(model_name='wmstransfer', old_name='destination', new_name='archived_destination'),
        migrations.RenameField(model_name='wmssettings', old_name='transfer_location', new_name='archived_transfer_location'),
        migrations.RenameField(model_name='wmssettings', old_name='picking_last_location', new_name='archived_picking_last_location'),
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
