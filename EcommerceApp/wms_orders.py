"""Webshop, B2B and manual orders shared with the independent WMS."""
from .models import Order, WMSOrder
from django.db.models import Q


def panel_active_orders():
    return Order.objects.filter(izvor__in=[Order.Izvor.WEBSHOP, Order.Izvor.MAGACIN]).exclude(
        status__in=[Order.Status.CEKA_PLACANJE, Order.Status.ZAVRSENA, Order.Status.OTKAZANA],
    ).exclude(
        Q(ime_prezime__iexact='Prenos u MP') | Q(pick_state__kind__isnull=False, pick_state__kind='prenos_mp'),
    ).exclude(lager_status__in=[Order.LagerStatus.VALIDIRANO, Order.LagerStatus.OTKAZANO]).exclude(
        wms_order__status__in=['pakovanje', 'zapakovana', 'otkazana'],
    )


def sync_panel_orders():
    from .magacin import is_vp_order
    for order in panel_active_orders().filter(wms_order__isnull=True).select_related('b2b_submission__account').iterator(chunk_size=200):
        submission = getattr(order, 'b2b_submission', None)
        account = submission.account if submission else None
        WMSOrder.objects.get_or_create(source_order=order, defaults={
            'tip': 'vp' if submission or is_vp_order(order) else 'online',
            'kupac': (account.company or order.ime_prezime) if account else order.ime_prezime,
            'email': account.email if account else order.email,
            'telefon': account.telefon if account else order.telefon,
            'adresa': account.adresa if account else order.adresa,
            'grad': account.grad if account else order.grad,
            'postanski_broj': account.postanski_broj if account else order.postanski_broj,
            'napomena': order.napomena,
        })
