"""Read-only, deterministic fingerprint of existing local warehouse state.

No model save(), load(), seed, sync, normalization or stock refresh is called.
The sole derived value is computed through the existing Cart.availability().
"""
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections, transaction

SCHEMA = 'local-warehouse-v1'
BASE_MODELS = {
    'Product', 'ProductVariation', 'ProductImage', 'ProductWarehouseMeta',
    'Brand', 'Category', 'Tag', 'Order', 'OrderItem', 'OrderStockHold',
    'Uvoz', 'UvozStavka', 'NivelacijaOznaka', 'BarcodeConflict',
    'ManualOrderDraft', 'ManualOrderDraftRevision', 'LocationCleaningRequest',
    'CardPayment', 'MungosProductMapping',
}


def encoded(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True,
                      separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


@contextmanager
def read_only_snapshot(using='default'):
    """Consistent snapshot; database itself refuses writes inside this block."""
    connection = connections[using]
    if connection.vendor not in ('postgresql', 'sqlite'):
        raise ValueError('Read-only audit supports PostgreSQL and SQLite only.')
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA query_only')
            previous = cursor.fetchone()[0]
            cursor.execute('PRAGMA query_only=ON')
        previous_mode = connection.transaction_mode
        connection.transaction_mode = 'DEFERRED'
        try:
            with transaction.atomic(using=using):
                yield
        finally:
            connection.transaction_mode = previous_mode
            with connection.cursor() as cursor:
                cursor.execute(f'PRAGMA query_only={int(previous)}')
    else:
        if connection.in_atomic_block:
            raise ValueError('Production audit requires its own top-level read-only transaction.')
        with transaction.atomic(using=using):
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            yield


def relevant_models():
    result = {}
    for model in apps.get_app_config('EcommerceApp').get_models():
        name = model.__name__
        if name in BASE_MODELS or name.startswith(('Warehouse', 'Magacin', 'B2B')):
            if not model._meta.proxy:
                result[model._meta.label] = model
                for field in model._meta.many_to_many:
                    through = field.remote_field.through
                    result[through._meta.label] = through
    return [result[key] for key in sorted(result)]


def warehouse_snapshot():
    from .cart import Cart
    from .models import Product, ProductVariation, WarehouseStock, WarehouseLocation, OrderStockHold

    with read_only_snapshot():
        tables = {}
        for model in relevant_models():
            fields = sorted(field.attname for field in model._meta.concrete_fields)
            sha = hashlib.sha256()
            sha.update(encoded({'model': model._meta.label, 'fields': fields}).encode('utf-8'))
            count = 0
            for row in model._base_manager.using('default').order_by(model._meta.pk.attname).values(*fields).iterator(chunk_size=1000):
                sha.update(b'\n')
                sha.update(encoded(row).encode('utf-8'))
                count += 1
            tables[model._meta.label] = {'count': count, 'sha256': sha.hexdigest()}
        per_sku, per_product = {}, {}
        per_location = {str(pk): {'quantity': 0, 'reserved': 0} for pk in WarehouseLocation.objects.values_list('pk', flat=True)}
        quantity = reserved = 0
        for row in WarehouseStock.objects.order_by('pk').values('product_id', 'variation_id', 'location_id', 'kolicina', 'rezervisano').iterator():
            quantity += row['kolicina']
            reserved += row['rezervisano']
            sku = f'{row["product_id"]}:{row["variation_id"] or 0}'
            location = str(row['location_id'])
            for bucket, key in ((per_sku, sku), (per_product, str(row['product_id'])), (per_location, location)):
                target = bucket.setdefault(key, {'quantity': 0, 'reserved': 0})
                target['quantity'] += row['kolicina']
                target['reserved'] += row['rezervisano']
        products = Product.objects.in_bulk()
        variants = ProductVariation.objects.in_bulk()
        cart = Cart(SimpleNamespace(session={}))
        cart.cart = {f'{pk}:0': {'product_id': pk, 'variation_id': None, 'quantity': 1} for pk in products}
        cart.cart.update({f'{v.artikal_id}:{pk}': {'product_id': v.artikal_id, 'variation_id': pk, 'quantity': 1} for pk, v in variants.items()})
        available = cart.availability(products=products, variants=variants)
        holds = list(OrderStockHold.objects.order_by('pk').values('id', 'narudzba_id', 'product_id', 'variation_id', 'location_id', 'kolicina'))
        # Every existing hold is reported; no inferred cleanup of stale holds.
        summary = {'quantity': quantity, 'warehouse_reserved': reserved,
                   'per_product_variation': per_sku, 'per_product': per_product, 'per_location': per_location,
                   'catalog_quantities': {**{f'{pk}:0': {'quantity': p.stanje, 'in_stock': p.na_stanju} for pk, p in products.items()},
                                          **{f'{v.artikal_id}:{pk}': {'quantity': v.stanje, 'in_stock': v.na_stanju} for pk, v in variants.items()}},
                   'active_holds': holds, 'hold_quantity': sum(row['kolicina'] for row in holds),
                   'availability': available}
        content = {'schema': SCHEMA, 'tables': tables, 'warehouse': summary}
        return {**content, 'fingerprint': digest(content)}
