from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('bera_control', 'ControlState').objects.using(schema_editor.connection.alias).get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('bera_control', '0001_initial')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
