from .models import Category, Product


def get_category_ids_with_products():
    """Kategorije koje imaju barem jedan aktivan artikal na stanju (u sebi ili podstablu)."""
    product_category_ids = Product.objects.filter(
        aktivan=True,
        na_stanju=True,
        kategorija_id__isnull=False,
    ).values_list('kategorija_id', flat=True).distinct()

    parent_map = dict(
        Category.objects.filter(aktivan=True).values_list('pk', 'roditelj_id')
    )

    populated = set()
    for category_id in product_category_ids:
        current = category_id
        while current:
            populated.add(current)
            current = parent_map.get(current)
    return populated


def filter_categories_with_products(queryset, populated_ids=None):
    populated_ids = populated_ids or get_category_ids_with_products()
    if not populated_ids:
        return queryset.none()
    return queryset.filter(pk__in=populated_ids)