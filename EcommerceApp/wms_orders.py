"""Webshop orders shared by the panel and independent WMS."""
from .models import Order, WMSOrder
from django.db.models import Q


def panel_active_orders():
    return Order.objects.filter(izvor=Order.Izvor.WEBSHOP).exclude(
        status__in=[Order.Status.CEKA_PLACANJE, Order.Status.ZAVRSENA, Order.Status.OTKAZANA],
    ).exclude(
        Q(ime_prezime__iexact='Prenos u MP') | Q(pick_state__kind__isnull=False, pick_state__kind='prenos_mp'),
    ).exclude(lager_status__in=[Order.LagerStatus.VALIDIRANO, Order.LagerStatus.OTKAZANO]).exclude(
        wms_order__status__in=['pakovanje', 'zapakovana', 'otkazana'],
    )


def sync_panel_orders():
    for order in panel_active_orders().filter(wms_order__isnull=True).iterator():
        WMSOrder.objects.get_or_create(source_order=order, defaults={
            'tip': 'online', 'kupac': order.ime_prezime, 'email': order.email,
            'telefon': order.telefon, 'adresa': order.adresa,
            'napomena': order.napomena,
        })
