from django.db import migrations


def protect(apps, schema_editor):
    from EcommerceApp.retention_triggers import install_history
    install_history(schema_editor.connection, baseline=True)


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0312_merge_warehouse_customer_names')]
    operations = [migrations.RunPython(protect)]
