"""Use existing module switches if this installation defines them."""
from django.apps import apps


def model_module(key):
    if key.startswith('b2b'):
        return 'b2b'
    return {'livevisitor': 'posjetioci_uzivo', 'livevisitoroffer': 'posjetioci_uzivo',
            'scratchprize': 'sretni_greb_greb', 'scratchclaim': 'sretni_greb_greb',
            'activecartitem': 'stavke_aktivnih_korpi', 'loyaltycard': 'loyalty',
            'loyaltypurchase': 'loyalty', 'giftvoucher': 'poklon_vaucer',
            'akcija': 'akcije', 'akcijabundleline': 'akcije', 'akcijaqtytier': 'akcije'}.get(key.lower())


def module_locked(field):
    if not field:
        return False
    try:
        model = apps.get_model('EcommerceApp', 'ModulePermissions')
    except LookupError:
        return False
    if field not in {item.name for item in model._meta.fields}:
        return False
    from django.db import connection
    if model._meta.db_table not in connection.introspection.table_names():
        return False
    return model.objects.filter(pk=1, **{field: False}).exists()


def panel_route_module(name):
    for prefix, field in [('staff_b2b_', 'b2b'), ('staff_scratch_', 'sretni_greb_greb'),
                          ('staff_active_cart', 'stavke_aktivnih_korpi'), ('staff_loyalty_', 'loyalty'),
                          ('staff_gift_voucher', 'poklon_vaucer'), ('staff_live_analytics', 'live_centar')]:
        if name.startswith(prefix):
            return field
    return None
