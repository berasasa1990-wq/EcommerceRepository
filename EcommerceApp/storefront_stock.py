"""Live warehouse availability shared by storefront rendering and cart limits."""
from copy import copy
from .models import WarehouseStock, ProductVariation


def warehouse_quantities(products):
    from .magacin import is_ignored_stock_location
    tracked, quantities = set(), {}
    for row in WarehouseStock.objects.filter(product_id__in=products).select_related('location'):
        tracked.add(row.product_id)
        if not row.location.aktivan or is_ignored_stock_location(row.location):
            continue
        sku = (row.product_id, row.variation_id)
        quantities[sku] = quantities.get(sku, 0) + max(0, int(row.kolicina or 0) - max(0, int(row.rezervisano or 0)))
    return tracked, quantities


def sku_quantity(product, variation=None):
    if product.is_set:
        from .product_sets import set_stock_totals
        return set_stock_totals(product)['dostupno']
    tracked, quantities = warehouse_quantities([product.pk])
    if product.pk in tracked or product.magacin_sync_at:
        return quantities.get((product.pk, variation.pk if variation else None), 0)
    target = variation or product
    return max(0, int(target.stanje or 0)) if target.na_stanju else 0


def storefront_products(products):
    """Return fresh presentation copies, never overwrite persisted catalog fields.

    Cached home-page product objects are deliberately not trusted for quantities.
    Stocks and variants are read in batches, regardless of the number of cards.
    """
    products = list(products)
    if not products:
        return []
    ids = [product.pk for product in products]
    tracked, quantities = warehouse_quantities(ids)
    variants = {}
    for variant in ProductVariation.objects.filter(artikal_id__in=ids).order_by('redoslijed', 'id'):
        variants.setdefault(variant.artikal_id, []).append(variant)
    result = []
    for original in products:
        product = copy(original)
        product._prefetched_objects_cache = dict(getattr(original, '_prefetched_objects_cache', {}))
        own_variants = variants.get(product.pk, [])
        managed = product.pk in tracked or bool(product.magacin_sync_at)
        if product.is_set:
            from .product_sets import set_stock_totals
            qty = set_stock_totals(product)['dostupno']
        elif managed:
            qty = sum(value for (pk, _), value in quantities.items() if pk == product.pk)
        else:
            qty = max(0, product.stanje) if product.na_stanju else 0
        product.stanje, product.na_stanju = qty, qty > 0
        for variant in own_variants:
            if managed:
                variant.stanje = quantities.get((product.pk, variant.pk), 0)
                variant.na_stanju = variant.stanje > 0
            variant.artikal = product
        variant_qs = ProductVariation.objects.filter(artikal_id=product.pk).order_by('redoslijed', 'id')
        variant_qs._result_cache = own_variants
        variant_qs._prefetch_done = True
        product._prefetched_objects_cache['varijacije'] = variant_qs
        result.append(product)
    return result
