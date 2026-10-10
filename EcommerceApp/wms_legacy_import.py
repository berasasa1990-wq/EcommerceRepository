"""Shared, atomic import from this site's legacy warehouse; source rows stay intact."""
from django.db import transaction
from .models import Product, ProductWMSStock, WarehouseLocation, WarehouseStock, WMSLocation


class MagacinImportError(ValueError):
    pass


@transaction.atomic
def import_magacin_locations(*, dry_run=False, backup_callback=None):
    source_rows = list(WarehouseStock.objects.select_for_update().values('product_id', 'location_id', 'variation_id', 'kolicina', 'rezervisano'))
    if any(row['kolicina'] < 0 for row in source_rows):
        raise MagacinImportError('Negativne zalihe nije moguće prenijeti u WMS bez korekcije.')
    if any(row['variation_id'] is not None for row in source_rows):
        raise MagacinImportError('WMS zaliha nema varijante; prenos bi izgubio raspored po varijantama.')
    locations = list(WarehouseLocation.objects.select_for_update().order_by('redoslijed', 'sifra', 'pk'))
    grouped = {}
    for row in source_rows:
        key = (row['product_id'], row['location_id'])
        grouped[key] = grouped.get(key, 0) + row['kolicina']
    stocks = [{'product_id': key[0], 'location_id': key[1], 'quantity': qty} for key, qty in sorted(grouped.items())]
    product_ids = {row['product_id'] for row in stocks}
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=product_ids)}
    existing_locations = {loc.naziv: loc for loc in WMSLocation.objects.select_for_update()}
    codes = {loc.pk: loc.sifra for loc in locations}
    desired = {(row['product_id'], codes[row['location_id']]): row['quantity'] for row in stocks}
    existing_stocks = list(ProductWMSStock.objects.select_for_update().filter(product_id__in=product_ids).select_related('lokacija'))
    for stock in existing_stocks:
        key = (stock.product_id, stock.lokacija.naziv)
        if key not in desired or stock.kolicina != desired[key]:
            raise MagacinImportError(f'Artikal {stock.product_id} već ima drugačiju WMS zalihu; ništa nije upisano.')
    for product in products.values():
        if product.wms_lokacija_id is not None:
            name = next((loc.naziv for loc in existing_locations.values() if loc.pk == product.wms_lokacija_id), None)
            if (product.pk, name) not in desired:
                raise MagacinImportError(f'Artikal {product.pk} već ima drugačiju glavnu WMS lokaciju; ništa nije upisano.')
    summary = {'legacy_locations': len(locations), 'products': len(products), 'stock_rows': len(stocks), 'physical_quantity': sum(row['quantity'] for row in stocks), 'legacy_reserved_quantity': sum(row['rezervisano'] for row in source_rows)}
    if dry_run:
        return {'dry_run': True, **summary}
    before = {
        'summary': summary,
        'wms_locations': list(WMSLocation.objects.values('id', 'naziv', 'opis')),
        'product_locations': [{'id': p.pk, 'wms_lokacija_id': p.wms_lokacija_id} for p in products.values()],
        'wms_stocks': [{'id': s.pk, 'product_id': s.product_id, 'lokacija_id': s.lokacija_id, 'kolicina': s.kolicina} for s in existing_stocks],
    }
    if backup_callback is not None:
        backup_callback(before)
    mapped = {}
    for loc in locations:
        target = existing_locations.get(loc.sifra)
        if target is None:
            description = '\n'.join(part for part in (loc.naziv if loc.naziv != loc.sifra else '', loc.opis) if part)
            target = WMSLocation.objects.create(naziv=loc.sifra, opis=description[:240])
        mapped[loc.pk] = target
    existing_keys = {(s.product_id, s.lokacija_id) for s in existing_stocks}
    new_rows = [ProductWMSStock(product_id=row['product_id'], lokacija_id=mapped[row['location_id']].pk, kolicina=row['quantity']) for row in stocks if (row['product_id'], mapped[row['location_id']].pk) not in existing_keys]
    ProductWMSStock.objects.bulk_create(new_rows)
    # Preserve all legacy quantities, reservations and storefront stock flags.
    priority = {loc.pk: i for i, loc in enumerate(locations)}
    primary = {}
    for row in sorted(stocks, key=lambda row: (row['quantity'] == 0, priority[row['location_id']])):
        primary.setdefault(row['product_id'], mapped[row['location_id']].pk)
    changed_products = []
    for product in products.values():
        if product.wms_lokacija_id is None:
            product.wms_lokacija_id = primary[product.pk]
            changed_products.append(product)
    Product.objects.bulk_update(changed_products, ['wms_lokacija'])
    return {'applied': True, 'created_stock_rows': len(new_rows), 'assigned_primary_locations': len(changed_products), **summary}
