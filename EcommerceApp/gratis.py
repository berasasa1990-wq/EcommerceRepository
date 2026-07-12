from decimal import Decimal

from .models import Akcija, _izracunaj_akcijsku_od_postotka


def _resolve_product_variation(product):
    if not product.varijacije.exists():
        return None
    return product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id').first()


def _gratis_discounted_price(akcija, product, variation=None):
    prikazna = variation.prikazna_cijena if variation else product.prikazna_cijena
    if akcija.popust_postotak is None:
        return prikazna
    return _izracunaj_akcijsku_od_postotka(prikazna, akcija.popust_postotak)


def _product_is_available(product, variation=None):
    if variation:
        return True
    if product.varijacije.exists():
        return False
    return product.na_stanju


def get_active_gratis_akcija_for_product(product):
    """Aktivna + Gratis akcija za trigger artikal."""
    if not product:
        return None
    for akcija in Akcija.objects.filter(
        aktivan=True,
        tip=Akcija.Tip.GRATIS,
        artikal=product,
        gratis_artikal__isnull=False,
        popust_postotak__isnull=False,
    ).select_related('gratis_artikal').order_by('redoslijed', '-id'):
        if akcija.jos_traje() and akcija.gratis_artikal and akcija.gratis_artikal.aktivan:
            return akcija
    return None


def build_gratis_offer_response(akcija):
    """Podaci za modal ponude na stranici artikla."""
    gratis = akcija.gratis_artikal
    if not gratis:
        return None

    gratis_variation = _resolve_product_variation(gratis)
    if not _product_is_available(gratis, gratis_variation):
        return None

    prikazna = gratis_variation.prikazna_cijena if gratis_variation else gratis.prikazna_cijena
    snizena = _gratis_discounted_price(akcija, gratis, gratis_variation)
    if snizena is None:
        return None

    pct = format_gratis_pct(akcija)
    is_full = Decimal(str(akcija.popust_postotak or 0)) >= Decimal('100')
    slika_url = gratis.prikazna_slika.url if gratis.prikazna_slika else None

    return {
        'akcija_id': akcija.id,
        'gratis_naziv': gratis.naziv,
        'pct': pct,
        'is_full_discount': is_full,
        'slika_url': slika_url,
        'original_price': str(prikazna),
        'discounted_price': str(snizena),
    }


def _add_discounted_gratis_line(cart, akcija, gratis_product, *, quantity=1):
    variation = _resolve_product_variation(gratis_product)
    if not _product_is_available(gratis_product, variation):
        return False

    prikazna = variation.prikazna_cijena if variation else gratis_product.prikazna_cijena
    discounted = _gratis_discounted_price(akcija, gratis_product, variation)
    cart.add(
        gratis_product,
        variation=variation,
        quantity=quantity,
        custom_price=discounted,
        promo_bazna=prikazna,
        gratis_akcija_id=akcija.id,
    )
    return True


def apply_gratis_bundle_from_popup(cart, akcija, *, quantity=1):
    """Dodaj trigger i gratis artikal iz site pop-up ponude."""
    if (
        akcija.tip != Akcija.Tip.GRATIS
        or not akcija.gratis_popup
        or akcija.popust_postotak is None
    ):
        return None

    trigger = akcija.artikal
    gratis_product = akcija.gratis_artikal
    if not trigger or not gratis_product or not trigger.aktivan or not gratis_product.aktivan:
        return None

    trigger_variation = _resolve_product_variation(trigger)
    if not _product_is_available(trigger, trigger_variation):
        return None

    cart.add(trigger, variation=trigger_variation, quantity=quantity)
    if not _add_discounted_gratis_line(cart, akcija, gratis_product, quantity=quantity):
        return None
    return akcija


def _add_bundle_discounted_line(cart, akcija, product, *, quantity=1):
    """Dodaj artikal iz Pop-up bundle seta s % popustom."""
    variation = _resolve_product_variation(product)
    if not _product_is_available(product, variation):
        return False
    prikazna = variation.prikazna_cijena if variation else product.prikazna_cijena
    discounted = _gratis_discounted_price(akcija, product, variation)
    cart.add(
        product,
        variation=variation,
        quantity=quantity,
        custom_price=discounted,
        promo_bazna=prikazna,
        gratis_akcija_id=akcija.id,
    )
    return True


def apply_popup_bundle_from_popup(cart, akcija, *, quantity=1):
    """
    Pop-up bundle: svi artikli iz seta u korpu s istim % popusta.
    Linije s količinom (npr. A×2) se sabiru; set se smije dodavati više puta.
    """
    if akcija.tip != Akcija.Tip.BUNDLE or akcija.popust_postotak is None:
        return None

    sets = max(1, int(quantity or 1))
    rows = akcija.bundle_line_rows()
    if not rows:
        # legacy fallback
        products = akcija.bundle_products()
        if len(products) < 2:
            products = []
            if akcija.artikal_id and akcija.artikal and akcija.artikal.aktivan:
                products.append(akcija.artikal)
            if (
                akcija.gratis_artikal_id
                and akcija.gratis_artikal
                and akcija.gratis_artikal.aktivan
            ):
                products.append(akcija.gratis_artikal)
        rows = [{'product': p, 'quantity': 1} for p in products]

    unit_total = sum(max(1, int(r.get('quantity') or 1)) for r in rows)
    if unit_total < 2:
        return None

    added_units = 0
    for row in rows:
        product = row['product']
        line_qty = max(1, int(row.get('quantity') or 1)) * sets
        if _add_bundle_discounted_line(cart, akcija, product, quantity=line_qty):
            added_units += line_qty
    if added_units < 1:
        return None
    return akcija


def build_popup_bundle_message(akcija, *, quantity=1):
    products = akcija.bundle_products()
    if len(products) < 2:
        products = [p for p in (akcija.artikal, akcija.gratis_artikal) if p]
    if len(products) < 2:
        return 'Set je dodan u korpu.'
    pct = format_gratis_pct(akcija)
    names = ' + '.join(f'„{p.naziv}”' for p in products[:6])
    if len(products) > 6:
        names += '…'
    qty = max(1, int(quantity or 1))
    if qty > 1:
        return (
            f'Set {names} ×{qty} je dodan u korpu '
            f'({pct}% popusta na kompletan set).'
        )
    return (
        f'Set {names} je dodan u korpu ({pct}% popusta na kompletan set). '
        f'Možeš dodati set ponovo koliko želiš.'
    )


def format_gratis_pct(akcija):
    pct = akcija.popust_postotak
    if pct is None:
        return ''
    if pct == int(pct):
        return str(int(pct))
    return str(pct)


def build_gratis_popup_message(akcija):
    trigger = akcija.artikal
    gratis = akcija.gratis_artikal
    if not trigger or not gratis:
        return 'Artikli su dodani u korpu.'
    pct = format_gratis_pct(akcija)
    if Decimal(str(akcija.popust_postotak or 0)) >= Decimal('100'):
        discount_text = 'drugi artikal gratis'
    else:
        discount_text = f'{pct}% popusta na drugi artikal'
    return (
        f'"{trigger.naziv}" i "{gratis.naziv}" su dodani u korpu ({discount_text}).'
    )


def build_gratis_choice_message(akcija, *, accepted, trigger_label):
    gratis = akcija.gratis_artikal
    if accepted and gratis:
        pct = format_gratis_pct(akcija)
        if Decimal(str(akcija.popust_postotak or 0)) >= Decimal('100'):
            return f'"{trigger_label}" i "{gratis.naziv}" su dodani u korpu.'
        return f'"{trigger_label}" i "{gratis.naziv}" su dodani u korpu ({pct}% popusta na drugi artikal).'
    return f'"{trigger_label}" je dodano u korpu.'