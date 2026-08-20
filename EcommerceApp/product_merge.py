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
    # Ne briši odoo_template_id — potreban je za Odoo sync (npr. samo naziv).


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


def _standalone_name(parent_name, variation):
    parent_name = (parent_name or '').strip()
    var_name = (variation.naziv or '').strip()
    if not var_name:
        return parent_name[:200] or f'Artikal {parent.pk}'
    parent_cf = parent_name.casefold()
    var_cf = var_name.casefold()
    if not parent_name or var_cf == parent_cf or parent_cf in var_cf:
        return var_name[:200]
    if var_cf in parent_cf:
        return parent_name[:200]
    return f'{parent_name} — {var_name}'[:200]


def _unique_product_sifra(sifra, *, exclude_pk=None):
    raw = (sifra or '').strip()
    if not raw:
        return None
    candidate = raw[:SIFRA_MAX_LENGTH]
    n = 1
    qs = Product.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(sifra=candidate).exists():
        suffix = f'-{n}'
        candidate = f'{raw[: max(1, SIFRA_MAX_LENGTH - len(suffix))]}{suffix}'
        n += 1
    return candidate


def _clear_variation_uniques(variation):
    updates = []
    if variation.sifra:
        variation.sifra = None
        updates.append('sifra')
    if variation.odoo_template_id:
        variation.odoo_template_id = None
        updates.append('odoo_template_id')
    if variation.odoo_variant_id:
        variation.odoo_variant_id = None
        updates.append('odoo_variant_id')
    if updates:
        variation.save(update_fields=updates)


def _rewire_variation_to_product(variation, dest_product):
    from .models import (
        ActiveCartItem,
        MagacinPopisStavka,
        MagacinVpStavka,
        OrderItem,
        OrderStockHold,
        WarehouseMovement,
        WarehouseStock,
    )

    for stock in list(WarehouseStock.objects.filter(variation=variation)):
        existing = WarehouseStock.objects.filter(
            product=dest_product,
            variation_key=0,
            location_id=stock.location_id,
        ).exclude(pk=stock.pk).first()
        if existing:
            existing.kolicina = int(existing.kolicina or 0) + int(stock.kolicina or 0)
            existing.rezervisano = int(existing.rezervisano or 0) + int(stock.rezervisano or 0)
            existing.save(update_fields=['kolicina', 'rezervisano'])
            stock.delete()
        else:
            stock.product = dest_product
            stock.variation = None
            stock.save()
    WarehouseMovement.objects.filter(variation=variation).update(
        product=dest_product, variation=None,
    )
    OrderStockHold.objects.filter(variation=variation).update(
        product=dest_product, variation=None,
    )
    OrderItem.objects.filter(varijacija=variation).update(
        artikal=dest_product, varijacija=None,
    )
    ActiveCartItem.objects.filter(variation=variation).update(
        product=dest_product, variation=None,
    )
    MagacinVpStavka.objects.filter(variation=variation).update(
        product=dest_product, variation=None,
    )
    MagacinPopisStavka.objects.filter(variation=variation).update(
        product=dest_product, variation=None,
    )


def _apply_variation_fields(product, variation, *, sifra, odoo_template_id, source_name=None):
    product.naziv = _standalone_name(source_name if source_name is not None else product.naziv, variation)
    product.sifra = sifra
    if variation.cijena is not None:
        product.cijena = variation.cijena
    if variation.akcijska_cijena is not None:
        product.akcijska_cijena = variation.akcijska_cijena
    if variation.akcija_postotak is not None:
        product.akcija_postotak = variation.akcija_postotak
    if variation.pakovanje_komada:
        product.pakovanje_komada = variation.pakovanje_komada
    product.na_stanju = variation.na_stanju
    product.stanje = variation.stanje
    if variation.slika:
        product.slika = variation.slika
    if odoo_template_id and (
        not product.odoo_template_id
        or product.odoo_template_id == odoo_template_id
        or not Product.objects.filter(odoo_template_id=odoo_template_id).exclude(pk=product.pk).exists()
    ):
        product.odoo_template_id = odoo_template_id
    product.save()


def _product_from_variation(parent, variation, *, sifra, odoo_template_id, source_name=None):
    from .models import ProductWarehouseMeta

    product = Product(
        naziv=_standalone_name(source_name if source_name is not None else parent.naziv, variation),
        sifra=sifra,
        cijena=variation.cijena if variation.cijena is not None else parent.cijena,
        akcijska_cijena=variation.akcijska_cijena,
        akcija_postotak=variation.akcija_postotak,
        pakovanje_komada=variation.pakovanje_komada or parent.pakovanje_komada,
        na_stanju=variation.na_stanju,
        stanje=variation.stanje,
        slika=variation.slika or parent.slika,
        opis=parent.opis,
        kategorija=parent.kategorija,
        brend=parent.brend,
        aktivan=parent.aktivan,
        prikazi_na_pocetnoj=parent.prikazi_na_pocetnoj,
        je_novitet=parent.je_novitet,
        je_hit=parent.je_hit,
        prioritet_lagera=parent.prioritet_lagera,
        proizvedeno_u_japanu=parent.proizvedeno_u_japanu,
        odoo_template_id=odoo_template_id,
        magacin_sync_at=parent.magacin_sync_at,
        meta_title=parent.meta_title,
        meta_description=parent.meta_description,
    )
    product.save()
    product.tagovi.set(parent.tagovi.all())
    meta = getattr(parent, 'magacin_meta', None)
    if meta is not None:
        ProductWarehouseMeta.objects.get_or_create(
            product=product,
            defaults={
                'dobavljac': meta.dobavljac,
                'tezina': meta.tezina,
                'jedinica_mjere': meta.jedinica_mjere,
                'min_zaliha': meta.min_zaliha,
                'veleprodajna_cijena': meta.veleprodajna_cijena,
            },
        )
    return product


def _keeper_variation(product, variations):
    for variation in variations:
        if product.odoo_template_id and variation.odoo_template_id == product.odoo_template_id:
            return variation
    for variation in variations:
        if product.sifra and variation.sifra and variation.sifra == product.sifra:
            return variation
    return variations[0]


@transaction.atomic
def split_product_variations(product):
    """Svaka varijacija postaje zaseban artikal. Glavni artikal zadržava jednu."""
    product = Product.objects.select_related('kategorija', 'brend').prefetch_related(
        'varijacije', 'tagovi',
    ).get(pk=product.pk)
    variations = list(product.varijacije.all().order_by('redoslijed', 'id'))
    if not variations:
        raise ProductMergeError(f'„{product.naziv}” nema varijacija za rastavljanje.')

    keeper = _keeper_variation(product, variations)
    created_products = []
    source_name = product.naziv

    keeper_sifra = keeper.sifra
    keeper_odoo = keeper.odoo_template_id
    _clear_variation_uniques(keeper)
    _apply_variation_fields(
        product, keeper,
        sifra=_unique_product_sifra(keeper_sifra, exclude_pk=product.pk),
        odoo_template_id=keeper_odoo,
        source_name=source_name,
    )
    _rewire_variation_to_product(keeper, product)

    for variation in variations:
        if variation.pk == keeper.pk:
            continue
        sifra = variation.sifra
        odoo_id = variation.odoo_template_id
        _clear_variation_uniques(variation)
        new_product = _product_from_variation(
            product, variation,
            sifra=_unique_product_sifra(sifra, exclude_pk=product.pk),
            odoo_template_id=(
                odoo_id
                if odoo_id and not Product.objects.filter(odoo_template_id=odoo_id).exists()
                else None
            ),
            source_name=source_name,
        )
        _rewire_variation_to_product(variation, new_product)
        created_products.append(new_product)

    for variation in variations:
        variation.delete()

    return {
        'primary': product,
        'created_products': created_products,
        'split_count': 1 + len(created_products),
    }