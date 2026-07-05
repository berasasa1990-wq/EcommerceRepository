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
    """Aktivna + Gratis akcija za trigger artikal (popup ili automatski)."""
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


def get_gratis_promo_for_product(product):
    """Podaci za promo baner na stranici artikla."""
    akcija = get_active_gratis_akcija_for_product(product)
    if not akcija:
        return None

    gratis = akcija.gratis_artikal
    pct = format_gratis_pct(akcija)
    is_full = Decimal(str(akcija.popust_postotak or 0)) >= Decimal('100')
    return {
        'gratis_naziv': gratis.naziv,
        'pct': pct,
        'is_full_discount': is_full,
    }


def get_gratis_akcija_for_product(product):
    """Aktivna + Gratis akcija za automatsko dodavanje (bez pop-upa)."""
    if not product:
        return None
    for akcija in Akcija.objects.filter(
        aktivan=True,
        tip=Akcija.Tip.GRATIS,
        gratis_popup=False,
        artikal=product,
        gratis_artikal__isnull=False,
        popust_postotak__isnull=False,
    ).select_related('gratis_artikal').order_by('redoslijed', '-id'):
        if akcija.jos_traje():
            return akcija
    return None


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


def apply_gratis_for_cart_add(cart, trigger_product, *, quantity=1):
    """Dodaj drugi artikal sa popustom kad se trigger doda u korpu."""
    akcija = get_gratis_akcija_for_product(trigger_product)
    if not akcija:
        return None

    gratis_product = akcija.gratis_artikal
    if not gratis_product or not gratis_product.aktivan:
        return None

    if _add_discounted_gratis_line(cart, akcija, gratis_product, quantity=quantity):
        return akcija
    return None


def apply_gratis_bundle_from_popup(cart, akcija, *, quantity=1):
    """Dodaj trigger i gratis artikal iz pop-up ponude."""
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


def build_auto_gratis_message(akcija):
    gratis = akcija.gratis_artikal
    if not gratis:
        return ''
    pct = format_gratis_pct(akcija)
    if Decimal(str(akcija.popust_postotak or 0)) >= Decimal('100'):
        return f' Gratis: "{gratis.naziv}" je automatski dodano u korpu.'
    return f' "{gratis.naziv}" je automatski dodano u korpu sa {pct}% popusta.'