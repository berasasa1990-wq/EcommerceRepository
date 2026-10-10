from django.db import migrations


def sync_mode(apps, schema_editor):
    db = schema_editor.connection.alias
    config = apps.get_model('EcommerceApp', 'ModulePermissions').objects.using(db).filter(pk=1).first()
    if config is None:
        return
    products = apps.get_model('EcommerceApp', 'Product').objects.using(db)
    products.update(pracenje_zaliha=config.wms_zalihe)
    if config.wms_zalihe:
        products.filter(stanje=0).update(na_stanju=False, modul_sakriven=True)
        products.filter(stanje__gt=0).update(na_stanju=True, modul_sakriven=False)
    else:
        products.update(modul_sakriven=False)


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0334_merge_wms_stock_locations')]
    operations = [migrations.RunPython(sync_mode, migrations.RunPython.noop)]
