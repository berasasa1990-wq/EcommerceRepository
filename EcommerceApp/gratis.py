from decimal import Decimal

from .models import Akcija, ProductVariation


def get_gratis_akcija_for_product(product):
    if not product:
        return None
    for akcija in Akcija.objects.filter(
        aktivan=True,
        tip=Akcija.Tip.GRATIS,
        artikal=product,
        gratis_artikal__isnull=False,
    ).select_related('gratis_artikal').order_by('redoslijed', '-id'):
        if akcija.jos_traje():
            return akcija
    return None


def _resolve_gratis_variation(product):
    if not product.varijacije.exists():
        return None
    return product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id').first()


def apply_gratis_for_cart_add(cart, trigger_product, *, quantity=1):
    """Dodaj gratis artikal kad se trigger doda u korpu."""
    akcija = get_gratis_akcija_for_product(trigger_product)
    if not akcija:
        return None

    gratis_product = akcija.gratis_artikal
    if not gratis_product or not gratis_product.aktivan:
        return None

    variation = _resolve_gratis_variation(gratis_product)
    if gratis_product.varijacije.exists() and not variation:
        return None
    if not variation and not gratis_product.na_stanju:
        return None

    prikazna = variation.prikazna_cijena if variation else gratis_product.prikazna_cijena
    cart.add(
        gratis_product,
        variation=variation,
        quantity=quantity,
        custom_price=Decimal('0'),
        promo_bazna=prikazna,
        gratis_akcija_id=akcija.id,
    )
    return akcija