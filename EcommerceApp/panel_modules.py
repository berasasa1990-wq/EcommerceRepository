from .models import ModulePermissions


def model_module(key):
    if key.startswith('b2b'):
        return 'b2b'
    return {
        'livevisitor': 'posjetioci_uzivo', 'livevisitoroffer': 'posjetioci_uzivo',
        'scratchprize': 'sretni_greb_greb', 'scratchclaim': 'sretni_greb_greb',
        'activecartitem': 'stavke_aktivnih_korpi',
        'loyaltycard': 'loyalty', 'loyaltypurchase': 'loyalty',
        'giftvoucher': 'poklon_vaucer',
        'akcija': 'akcije', 'akcijabundleline': 'akcije', 'akcijaqtytier': 'akcije',
    }.get(key.lower())


def module_locked(field):
    return bool(field and ModulePermissions.objects.filter(pk=1, **{field: False}).exists())


def panel_route_module(name, section=None):
    if name in {'public_loyalty_card_image', 'staff_magacin_loyalty_telefon', 'magacin_loyalty_telefon'}:
        return 'loyalty'
    if section:
        return model_module(section)
    for prefix, field in (
        ('staff_b2b_', 'b2b'), ('staff_scratch_', 'sretni_greb_greb'),
        ('scratch_', 'sretni_greb_greb'),
        ('staff_active_cart', 'stavke_aktivnih_korpi'),
        ('staff_loyalty_', 'loyalty'), ('staff_gift_voucher', 'poklon_vaucer'),
        ('staff_live_analytics', 'live_centar'),
    ):
        if name.startswith(prefix):
            return field
    # Model editor URL names also cover add/change/delete/history actions.
    if name.startswith('EcommerceApp_'):
        return model_module(name.split('_')[1])
    return None
