"""Selection of + % promotions shared by the product page and cart."""
from .models import Akcija, Category


def offers_for_products(products):
    products = list(products)
    if not products:
        return {}
    parents = dict(Category.objects.values_list('pk', 'roditelj_id'))
    ancestors = {}
    for product in products:
        ids = set()
        category_id = product.kategorija_id
        while category_id and category_id not in ids:
            ids.add(category_id)
            category_id = parents.get(category_id)
        ancestors[product.pk] = ids
    offers = {}
    actions = Akcija.objects.filter(
        tip=Akcija.Tip.PLUS_PERCENT, aktivan=True,
        popust_postotak__gt=0, popust_postotak__lte=100,
    ).prefetch_related('plus_artikli').order_by('redoslijed', '-pk')
    for action in actions:
        selected = {p.pk for p in action.plus_artikli.all()}
        for product in products:
            if product.pk not in offers and (
                product.pk in selected or action.kategorija_id in ancestors[product.pk]
            ):
                offers[product.pk] = action
    return offers


def discounts_for_cart(lines, offers, available):
    """Use the strongest active offer, keeping its trigger at its current price."""
    triggers = [pid for pid in offers if any(
        line['product_id'] == pid and available.get(key, 0) > 0
        for key, line in lines.items()
    )]
    if not triggers:
        return {}
    trigger = max(triggers, key=lambda pid: offers[pid].popust_postotak)
    return {
        key: offers[trigger] for key, line in lines.items()
        if line['product_id'] != trigger and available.get(key, 0) > 0
    }
