"""Use legacy warehouse services as the stock authority for imported WMS pages."""
from contextvars import ContextVar
from functools import wraps
import hashlib
from django.contrib import messages
from django.db import connection, transaction
from django.db.models import Sum
from django.db.models.signals import post_save, post_delete
from django.http import HttpResponse
from django.shortcuts import redirect
from .models import WMSLocation, ProductWMSStock, ProductWMSLocationMeta, WarehouseLocation, WarehouseStock, Product, WMSOrder
from .magacin import MagacinError, set_location_counted_qty, is_ignored_stock_location, apply_movement
from .models import WarehouseMovement
from .request_performance import schema_tables

_changed = ContextVar('wms_changed_products', default=None)
_canonical_schemas = set()


def canonical_schema_ready():
    # Cache only a successful schema check, never stocks, users or permissions.
    key = (connection.alias, connection.settings_dict['ENGINE'], str(connection.settings_dict['NAME']), connection.settings_dict.get('HOST'))
    if key in _canonical_schemas:
        return True
    from .models import WMSPickLine
    with connection.cursor() as cursor:
        columns = connection.introspection.get_table_description(cursor, WMSPickLine._meta.db_table)
    ready = 'archived_location_id' in {column.name for column in columns}
    if ready:
        _canonical_schemas.add(key)
    return ready


def stock_changed(sender, instance, **kwargs):
    changed = _changed.get()
    if changed is not None:
        changed.add(instance.product_id)


post_save.connect(stock_changed, sender=ProductWMSStock, weak=False, dispatch_uid='bera_wms_stock_save')
post_delete.connect(stock_changed, sender=ProductWMSStock, weak=False, dispatch_uid='bera_wms_stock_delete')


def legacy_location(location):
    if isinstance(location, WarehouseLocation):
        return location
    if location.legacy_location_id:
        return location.legacy_location
    code = location.naziv if len(location.naziv) <= 20 else 'WMS-' + hashlib.sha256(location.naziv.encode()).hexdigest()[:16]
    legacy, _ = WarehouseLocation.objects.get_or_create(sifra=code, defaults={'naziv': location.naziv})
    location.legacy_location = legacy
    location.save(update_fields=['legacy_location'])
    return legacy


def import_legacy_stock(product_ids=None):
    rows = WarehouseStock.objects.select_related('location')
    if product_ids is not None:
        rows = rows.filter(product_id__in=product_ids)
    grouped = {}
    locations = {}
    for row in rows:
        if is_ignored_stock_location(row.location):
            continue
        locations[row.location_id] = row.location
        key = (row.product_id, row.location_id)
        grouped[key] = grouped.get(key, 0) + max(0, row.kolicina)
    mapped = {}
    existing_locations = {}
    for row in WMSLocation.objects.filter(legacy_location_id__in=locations).order_by('pk'):
        existing_locations.setdefault(row.legacy_location_id, row)
    for pk, location in locations.items():
        wms = existing_locations.get(pk)
        if wms is None:
            wms, _ = WMSLocation.objects.get_or_create(naziv=location.sifra, defaults={'legacy_location': location})
            if not wms.legacy_location_id:
                wms.legacy_location = location
                wms.save(update_fields=['legacy_location'])
        mapped[pk] = wms.pk
    desired = {(product_id, mapped[location_id]): quantity for (product_id, location_id), quantity in grouped.items()}
    existing = ProductWMSStock.objects.filter(lokacija__legacy_location__isnull=False)
    if product_ids is not None:
        existing = existing.filter(product_id__in=product_ids)
    changed = []
    for stock in existing:
        quantity = desired.pop((stock.product_id, stock.lokacija_id), 0)
        if stock.kolicina != quantity:
            stock.kolicina = quantity
            changed.append(stock)
    if changed:
        ProductWMSStock.objects.bulk_update(changed, ['kolicina'])
    ProductWMSStock.objects.bulk_create([ProductWMSStock(product_id=product_id, lokacija_id=location_id, kolicina=quantity)
                                       for (product_id, location_id), quantity in desired.items()], ignore_conflicts=True)


def apply_wms_stock(product_ids, user, *, note='BERA WMS promjena zalihe'):
    for product in Product.objects.select_for_update().filter(pk__in=product_ids):
        desired = {}
        for stock in product.wms_zalihe.select_related('lokacija'):
            location = legacy_location(stock.lokacija)
            desired[location.pk] = (location, stock.kolicina)
        old_rows = list(product.magacin_zalihe.select_for_update().select_related('location'))
        for row in old_rows:
            if not is_ignored_stock_location(row.location):
                desired.setdefault(row.location_id, (row.location, 0))
        decreases, increases = [], []
        for location_id, (location, quantity) in desired.items():
            rows = [row for row in old_rows if row.location_id == location_id]
            old_quantity = sum(max(0, row.kolicina) for row in rows)
            if quantity == old_quantity:
                continue
            if any(row.variation_id for row in rows):
                raise MagacinError('Zalihe varijacija uredite kroz postojeći magacin; raspored varijacija nije promijenjen.')
            if quantity < sum(max(0, row.rezervisano) for row in rows):
                raise MagacinError('Ova količina je rezervisana za narudžbu. Prvo obradite narudžbu.')
            if quantity < old_quantity:
                decreases.append([location, old_quantity - quantity])
            else:
                increases.append([location, quantity - old_quantity])
        # Transfers use the existing transfer service, keeping visibility and
        # restock notifications stable while moving physical stock.
        for source in decreases:
            for destination in increases:
                take = min(source[1], destination[1])
                if take:
                    apply_movement(product=product, location=source[0], to_location=destination[0],
                                   tip=WarehouseMovement.Tip.TRANSFER, kolicina=take,
                                   user=user, napomena='BERA WMS prenos robe')
                    source[1] -= take
                    destination[1] -= take
        for location, quantity in desired.values():
            set_location_counted_qty(location=location, product=product, qty=quantity, user=user, napomena=note)


def complete_legacy_orders(user, order_ids):
    from .magacin import validate_order_stock
    completed_products = set()
    for wms in WMSOrder.objects.select_for_update().filter(pk__in=order_ids, status='zapakovana', source_order__isnull=False).select_related('source_order'):
        order = wms.source_order
        if order.stanje_skinuto or order.lager_status == order.LagerStatus.VALIDIRANO:
            continue
        state = {}
        counts = {}
        for line in wms.pick_lines.select_related('location', 'item'):
            if not line.item_id:
                continue
            location = legacy_location(line.location)
            state[f'{line.item_id}:{line.pk}'] = {'item_id': line.item_id, 'loc': location.sifra, 'got': line.confirmed_quantity, 'done': line.confirmed_at is not None}
            counts[line.item_id] = counts.get(line.item_id, 0) + line.confirmed_quantity
            completed_products.add(line.item.artikal_id)
        for item in order.stavke.filter(is_set_parent=False):
            item.kolicina_pokupljeno = counts.get(item.pk, 0)
            item.save(update_fields=['kolicina_pokupljeno'])
        order.pick_state = state
        order.save(update_fields=['pick_state'])
        # Legacy fulfillment removes unpicked items from the invoice. Preserve
        # their picking history on a WMS snapshot before that deletion.
        from .models import WMSOrderItem
        for item in order.stavke.filter(kolicina_pokupljeno=0):
            lines = wms.pick_lines.filter(item=item)
            if not lines.exists():
                continue
            snapshot = WMSOrderItem.objects.create(
                order=wms, artikal=item.artikal, naziv=item.naziv,
                varijacija_id=item.varijacija_id,
                sifra=item.sifra, kolicina=item.kolicina,
                bazna_cijena=item.bazna_cijena, cijena=item.cijena,
                popust=item.popust_postotak or 0,
            )
            lines.update(item=None, wms_item=snapshot)
        validate_order_stock(order, user=user)
    return completed_products


def warehouse_compatible(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
            return view(request, *args, **kwargs)
        tables = schema_tables()
        if any(model._meta.db_table not in tables for model in (WMSOrder, WMSLocation)):
            return HttpResponse('WMS zahtijeva svoje migracije. Postojeći magacin ostaje dostupan.', status=503)
        if not canonical_schema_ready():
            return HttpResponse('WMS direktni magacin zahtijeva migraciju 0315. Stari magacin ostaje dostupan na /nalog/magacin/.', status=503)
        try:
            if request.method != 'POST':
                return view(request, *args, **kwargs)
            with transaction.atomic():
                finishing = []
                if view.__name__ == 'pick_order' and request.POST.get('action') == 'finish':
                    finishing = list(WMSOrder.objects.filter(pk=kwargs.get('pk'), status__in=['nova', 'pakovanje']).values_list('pk', flat=True))
                response = view(request, *args, **kwargs)
                complete_legacy_orders(request.user, finishing)
                return response
        except MagacinError as error:
            retained = [message for message in messages.get_messages(request) if message.level != messages.SUCCESS]
            for message in retained:
                messages.add_message(request, message.level, str(message))
            messages.error(request, str(error))
            return redirect(request.path)
    return wrapped
