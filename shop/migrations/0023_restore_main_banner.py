from importlib import import_module
from django.db import migrations


def restore_main_banner(apps, schema_editor):
    import_module('shop.migrations.0020_existing_home_hero').create_existing_hero(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [('shop', '0022_alter_banner_options_alter_banner_position_and_more')]
    operations = [migrations.RunPython(restore_main_banner, migrations.RunPython.noop)]
