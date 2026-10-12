"""Warehouse access is controlled by the active superuser role."""


def warehouse_user_required(user):
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def can_access_route(user, name):
    return bool(
        name.startswith(('staff_magacin', 'staff_wms', 'staff_order_'))
        and warehouse_user_required(user)
    )


def warehouse_landing(user):
    return 'staff_wms' if warehouse_user_required(user) else 'account'
