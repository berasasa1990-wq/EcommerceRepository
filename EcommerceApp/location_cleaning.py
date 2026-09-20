"""Review picking shortages without treating them as inventory corrections."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import LocationCleaningRequest, Order, OrderStockHold, Product
from .magacin import MagacinError, _clearable_pick_locations, apply_movement


def record_shortages(order, state, *, user=None):
    items = {item.pk: item for item in order.stavke.select_related('artikal', 'varijacija')}
    for row in state.values():
        if not isinstance(row, dict) or not row.get('done'):
            continue
        item = items.get(row.get('item_id'))
        if not item or not item.artikal_id:
            continue
        needed, picked = int(row.get('need') or 0), int(row.get('got') or 0)
        if not 0 <= picked < needed:
            continue
        for location in _clearable_pick_locations(row.get('loc') or '', item.artikal, item.varijacija):
            entry, created = LocationCleaningRequest.objects.get_or_create(
                order=order, item_id_snapshot=item.pk, location=location,
                defaults=dict(order_number=order.broj, product=item.artikal, variation=item.varijacija,
                              name=item.puni_naziv, sku=item.sifra or '', needed=needed, picked=picked,
                              created_by=user if getattr(user, 'is_authenticated', False) else None),
            )
            if not created and not entry.decision and (entry.needed != needed or entry.picked != picked):
                entry.needed, entry.picked = needed, picked
                entry.save(update_fields=['needed', 'picked'])


@transaction.atomic
def resolve_request(pk, *, clear, user):
    snapshot = get_object_or_404(LocationCleaningRequest, pk=pk)
    order = Order.objects.select_for_update().get(pk=snapshot.order_id) if clear and snapshot.order_id else None
    entry = get_object_or_404(LocationCleaningRequest.objects.select_for_update(), pk=pk)
    if entry.decision:
        return entry
    if clear:
        # Picking deducts the physically collected units when the order is finished.
        # Do not erase those units before that transaction has completed.
        if order is not None:
            if order.lager_status not in {Order.LagerStatus.VALIDIRANO, Order.LagerStatus.OTKAZANO} and order.status != Order.Status.OTKAZANA:
                raise MagacinError('Prvo završi picking ove narudžbe, pa očisti lokaciju.')
        Product.objects.select_for_update().get(pk=entry.product_id)
        apply_movement(product=entry.product, variation=entry.variation, location=entry.location,
                       tip='korekcija', kolicina=0, user=user,
                       napomena=f'Zahtjev za čišćenje #{entry.pk}, narudžba #{entry.order_number}: {entry.reason} ({entry.picked}/{entry.needed})')
        OrderStockHold.objects.filter(product=entry.product, variation=entry.variation,
                                      location=entry.location, status=OrderStockHold.Status.REZERVISANO).update(status=OrderStockHold.Status.OTKAZANO)
    entry.decision = 'clear' if clear else 'keep'
    entry.resolved_at = timezone.now()
    entry.resolved_by = user
    entry.save(update_fields=['decision', 'resolved_at', 'resolved_by'])
    return entry
