"""Local product identifier validation."""
from .models import Product, ProductVariation


def _sifra_zauzeta(sifra, *, product_pk=None, variation_pk=None):
    product_qs = Product.objects.filter(sifra=sifra)
    if product_pk:
        product_qs = product_qs.exclude(pk=product_pk)
    if product_qs.exists():
        return True
    variation_qs = ProductVariation.objects.filter(sifra=sifra)
    if variation_pk:
        variation_qs = variation_qs.exclude(pk=variation_pk)
    return variation_qs.exists()
