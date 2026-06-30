from django.db import transaction

from .models import SIFRA_MAX_LENGTH, Product, ProductVariation


class ProductMergeError(Exception):
    pass


def sync_primary_stock(product):
    variations = list(product.varijacije.all())
    if not variations:
        return
    product.stanje = sum(variation.stanje for variation in variations)
    product.na_stanju = any(variation.na_stanju for variation in variations)
    product.odoo_template_id = None


def _variation_label(product):
    naziv = (product.naziv or '').strip()
    if len(naziv) > 100:
        return naziv[:100]
    return naziv or f'Varijanta {product.pk}'


def _find_variation_on_primary(primary, product):
    if product.odoo_template_id:
        variation = ProductVariation.objects.filter(
            artikal=primary,
            odoo_template_id=product.odoo_template_id,
        ).first()
        if variation:
            return variation
    if product.sifra:
        return ProductVariation.objects.filter(artikal=primary, sifra=product.sifra).first()
    return None


def _upsert_variation_from_product(primary, product, redoslijed):
    variation = _find_variation_on_primary(primary, product)
    values = {
        'naziv': _variation_label(product),
        'sifra': product.sifra,
        'cijena': product.cijena,
        'akcijska_cijena': product.akcijska_cijena,
        'na_stanju': product.na_stanju,
        'stanje': product.stanje,
        'odoo_template_id': product.odoo_template_id,
        'redoslijed': redoslijed,
    }

    if variation is None:
        variation = ProductVariation(artikal=primary, **values)
        variation.save()
        return variation, True

    for key, value in values.items():
        setattr(variation, key, value)
    variation.save()
    return variation, False


@transaction.atomic
def merge_products(selected_products, primary, *, new_name=None):
    selected = list(
        selected_products.select_related('kategorija', 'brend').prefetch_related('varijacije'),
    )
    if len(selected) < 2:
        raise ProductMergeError('Odaberite najmanje 2 artikla za spajanje.')

    primary = next((product for product in selected if product.pk == primary.pk), None)
    if primary is None:
        raise ProductMergeError('Glavni artikal mora biti među odabranim artiklima.')

    others = [product for product in selected if product.pk != primary.pk]

    if new_name:
        primary.naziv = new_name.strip()[:200]

    created_variations = 0
    updated_variations = 0
    redoslijed = primary.varijacije.count()

    for product in others:
        for variation in list(product.varijacije.all()):
            if variation.sifra:
                conflict = ProductVariation.objects.filter(
                    artikal=primary,
                    sifra=variation.sifra,
                ).exclude(pk=variation.pk).exists()
                if conflict:
                    variation.sifra = f'{variation.sifra}-{variation.pk}'[:SIFRA_MAX_LENGTH]
            variation.artikal = primary
            variation.redoslijed = redoslijed
            variation.save(update_fields=['artikal', 'redoslijed', 'sifra'])
            redoslijed += 1

    for product in selected:
        if product.pk != primary.pk and product.odoo_template_id:
            if ProductVariation.objects.filter(
                artikal=primary,
                odoo_template_id=product.odoo_template_id,
            ).exists():
                continue
        _, created = _upsert_variation_from_product(primary, product, redoslijed)
        if created:
            created_variations += 1
        else:
            updated_variations += 1
        redoslijed += 1

    if not primary.kategorija and any(product.kategorija_id for product in selected):
        primary.kategorija = next(product.kategorija for product in selected if product.kategorija_id)
    if not primary.brend and any(product.brend_id for product in selected):
        primary.brend = next(product.brend for product in selected if product.brend_id)
    if not primary.opis:
        primary.opis = next((product.opis for product in selected if product.opis), '')

    sync_primary_stock(primary)
    primary.save()

    deleted_count = 0
    for product in others:
        product.delete()
        deleted_count += 1

    return {
        'primary': primary,
        'created_variations': created_variations,
        'updated_variations': updated_variations,
        'deleted_products': deleted_count,
    }