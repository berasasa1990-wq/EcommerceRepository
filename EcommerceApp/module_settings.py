"""Global inventory module; absent configuration preserves existing stock modes."""
def inventory_mode():
    from .models import ModulePermissions
    return ModulePermissions.objects.filter(pk=1).values_list('wms_zalihe', flat=True).first()


def apply_inventory_mode(enabled):
    from .models import Product
    if enabled:
        Product.objects.update(pracenje_zaliha=True)
        Product.objects.filter(stanje=0).update(na_stanju=False, modul_sakriven=False)
        Product.objects.filter(stanje__gt=0).update(na_stanju=True, modul_sakriven=False)
    else:
        Product.objects.update(pracenje_zaliha=False, na_stanju=True, modul_sakriven=False)
