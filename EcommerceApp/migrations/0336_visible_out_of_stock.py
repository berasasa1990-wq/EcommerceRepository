from django.db import migrations


def show_out_of_stock(apps, schema_editor):
    db = schema_editor.connection.alias
    config = apps.get_model('EcommerceApp', 'ModulePermissions').objects.using(db).filter(pk=1).first()
    if config and config.wms_zalihe:
        products = apps.get_model('EcommerceApp', 'Product').objects.using(db)
        products.filter(stanje=0).update(pracenje_zaliha=True, na_stanju=False, modul_sakriven=False)


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0335_wms_inventory_mode')]
    operations = [migrations.RunPython(show_out_of_stock, migrations.RunPython.noop)]
