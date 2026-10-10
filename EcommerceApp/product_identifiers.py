"""Local product identifier validation."""
from .models import Product, ProductVariation


def normalized_product_name(value):
    return ' '.join((value or '').split()).casefold()


def product_name_taken(name, *, product_pk=None):
    normalized = normalized_product_name(name)
    if not normalized:
        return False
    products = Product.objects.exclude(pk=product_pk) if product_pk else Product.objects.all()
    return any(normalized_product_name(existing) == normalized
               for existing in products.values_list('naziv', flat=True).iterator())


def _sifra_zauzeta(sifra, *, product_pk=None, variation_pk=None):
    normalized = (sifra or '').strip().casefold()
    if not normalized:
        return False
    product_qs = Product.objects.exclude(sifra__isnull=True).exclude(sifra='')
    if product_pk:
        product_qs = product_qs.exclude(pk=product_pk)
    if any(value.strip().casefold() == normalized for value in product_qs.values_list('sifra', flat=True).iterator()):
        return True
    variation_qs = ProductVariation.objects.exclude(sifra__isnull=True).exclude(sifra='')
    if variation_pk:
        variation_qs = variation_qs.exclude(pk=variation_pk)
    return any(value.strip().casefold() == normalized for value in variation_qs.values_list('sifra', flat=True).iterator())
