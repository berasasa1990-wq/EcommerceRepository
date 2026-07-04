from .models import Akcija, Product, _izracunaj_akcijsku_od_postotka


def _active_korpa_nudjenje_offers():
    return list(
        Akcija.objects.filter(
            tip=Akcija.Tip.KORPA_NUDJENJE,
            aktivan=True,
            artikal__isnull=False,
            kategorija__isnull=False,
            popust_postotak__isnull=False,
            artikal__aktivan=True,
        )
        .select_related('artikal', 'kategorija')
        .order_by('redoslijed', '-id'),
    )


def _build_offered_product_context(akcija):
    product = akcija.artikal
    if not product:
        return None

    pct = akcija.popust_postotak
    variations = []
    in_stock = product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'naziv')
    if product.varijacije.exists():
        if not in_stock.exists():
            return None
    elif not product.na_stanju:
        return None
    for variation in in_stock:
        original = variation.prikazna_cijena
        discounted = _izracunaj_akcijsku_od_postotka(original, pct)
        if discounted is None:
            continue
        variations.append({
            'id': variation.id,
            'naziv': variation.naziv,
            'original_price': original,
            'discounted_price': discounted,
        })

    if variations:
        display_orig = min(item['original_price'] for item in variations)
        display_disc = min(item['discounted_price'] for item in variations)
    else:
        display_orig = product.prikazna_cijena
        display_disc = akcija.korpa_nudjenje_snizena_cijena(product)
        if display_disc is None:
            return None

    slika_url = None
    if product.prikazna_slika:
        slika_url = product.prikazna_slika.url

    return {
        'akcija_id': akcija.id,
        'product_id': product.pk,
        'slug': product.slug,
        'naziv': product.naziv,
        'slika_url': slika_url,
        'popust_postotak': pct,
        'original_price': display_orig,
        'discounted_price': display_disc,
        'has_variations': bool(variations),
        'variations': variations,
    }


def build_korpa_nudjenje_map(cart):
    """Mapa product_id (stavka u korpi) -> kontekst ponude."""
    if not cart.cart:
        return {}

    offers = _active_korpa_nudjenje_offers()
    if not offers:
        return {}

    product_ids = {item['product_id'] for item in cart.cart.values()}
    products = Product.objects.filter(pk__in=product_ids).select_related('kategorija')
    product_map = {product.pk: product for product in products}

    category_ids_by_offer = {
        offer.pk: set(offer.kategorija.get_descendant_ids())
        for offer in offers
        if offer.kategorija_id
    }

    suggestion_contexts = {
        offer.pk: _build_offered_product_context(offer)
        for offer in offers
    }

    result = {}
    for item in cart.cart.values():
        product_id = item['product_id']
        product = product_map.get(product_id)
        if not product or not product.kategorija_id:
            continue

        for offer in offers:
            if offer.artikal_id == product_id:
                continue
            if offer.artikal_id in product_ids:
                continue
            cat_ids = category_ids_by_offer.get(offer.pk)
            if not cat_ids or product.kategorija_id not in cat_ids:
                continue
            context = suggestion_contexts.get(offer.pk)
            if context:
                result[product_id] = context
                break

    return result