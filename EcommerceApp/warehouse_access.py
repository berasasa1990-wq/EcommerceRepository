"""Warehouse access is controlled by the active superuser role."""


def warehouse_user_required(user):
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def can_access_route(user, name):
    return bool(
        (name.startswith('staff_magacin') or name.startswith('staff_order_'))
        and warehouse_user_required(user)
        and not inventory_route_locked(name)
    )


def inventory_route_locked(name):
    from .module_settings import inventory_mode
    field = warehouse_module_field(name)
    if field:
        from .models import ModulePermissions
        return ModulePermissions.objects.filter(pk=1, **{field: False}).exists()
    protected = (
        'staff_magacin_artikl', 'staff_magacin_artikal',
        'staff_magacin_brzi_unos', 'staff_magacin_narudz',
        'staff_magacin_pakuj', 'staff_magacin_pakovanje', 'staff_magacin_kupci',
    )
    return name.startswith(protected) and inventory_mode() is False


def warehouse_module_field(name):
    prefixes = {
        'staff_magacin_uvoz': 'uvoz',
        'staff_magacin_provjera_lagera': 'provjera_lagera',
        'staff_magacin_lokacij': 'provjera_lagera',
        'staff_magacin_zalihe': 'provjera_lagera',
        'staff_magacin_transferi': 'provjera_lagera',
        'staff_magacin_rezervni_dijelovi': 'rezervni_dijelovi',
        'staff_magacin_setovi': 'artikli_u_setu',
        'staff_magacin_stampa_cijena': 'stampa_cijena',
        'staff_magacin_stampa_deklaracije': 'stampa_deklaracije',
        'staff_magacin_mp_dnevno': 'dnevno_skidanje',
        'staff_magacin_dupli_barkod': 'dupli_barkodovi',
        'staff_magacin_duguje': 'duguje_potrazuje',
        'staff_magacin_popis': 'popis_robe',
        'staff_magacin_fali_na_sajtu': 'fali_u_mp',
        'staff_magacin_nivelacije': 'nivelacije',
        'staff_magacin_ponud': 'kreiraj_ponudu',
        'staff_magacin_izvjestaji': 'izvjestaji',
    }
    return next((field for prefix, field in prefixes.items() if name.startswith(prefix)), None)


def warehouse_landing(user):
    if not warehouse_user_required(user):
        return 'account'
    return 'staff_magacin_pregled' if inventory_route_locked('staff_magacin_artikli') else 'staff_magacin_artikli'
