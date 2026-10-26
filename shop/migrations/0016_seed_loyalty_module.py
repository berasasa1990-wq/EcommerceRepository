from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('shop', 'SiteModule').objects.using(schema_editor.connection.alias).get_or_create(code='loyalty', defaults={'enabled': True})


class Migration(migrations.Migration):
    dependencies = [('shop', '0015_sitemodule')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
