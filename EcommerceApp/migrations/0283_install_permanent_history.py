from django.db import migrations


def install(apps, schema_editor):
    from EcommerceApp.retention_triggers import install_history
    install_history(schema_editor.connection, baseline=True)


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0282_permanent_system_history')]
    operations = [migrations.RunPython(install)]
