from decimal import Decimal, InvalidOperation

from .models import SiteSettings, _izracunaj_akcijsku_od_postotka

EXIT_POPUP_DISMISSED_KEY = 'cart_exit_popup_dismissed'


def _clamp_percent(value):
    try:
        percent = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        percent = Decimal('0')
    if percent < 0:
        return Decimal('0')
    if percent > 50:
        return Decimal('50')
    return percent.quantize(Decimal('0.01'))


def exit_popup_prices_for_product(product, percent, variation=None):
    prikazna = variation.prikazna_cijena if variation else product.prikazna_cijena
    if percent <= 0:
        return {
            'bazna': prikazna,
            'snizena': prikazna,
            'has_discount': False,
        }
    snizena = _izracunaj_akcijsku_od_postotka(prikazna, percent)
    if snizena is None or snizena >= prikazna:
        return {
            'bazna': prikazna,
            'snizena': prikazna,
            'has_discount': False,
        }
    return {
        'bazna': prikazna,
        'snizena': snizena,
        'has_discount': True,
    }


def get_cart_exit_popup_context(request, cart):
    postavke = SiteSettings.load()
    if not postavke.korpa_exit_popup_aktivan:
        return None
    if request.session.get(EXIT_POPUP_DISMISSED_KEY):
        return None

    product = postavke.korpa_exit_popup_artikal
    if not product or not product.aktivan:
        return None

    percent = _clamp_percent(postavke.korpa_exit_popup_popust)
    in_stock_variations = list(
        product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id'),
    )
    display_variation = in_stock_variations[0] if len(in_stock_variations) == 1 else None
    prices = exit_popup_prices_for_product(product, percent, display_variation)

    if product.varijacije.exists():
        can_add_directly = len(in_stock_variations) == 1
        is_available = bool(in_stock_variations)
    else:
        can_add_directly = True
        is_available = product.na_stanju

    return {
        'product': product,
        'discount_percent': percent if percent > 0 else None,
        'product_prices': prices,
        'can_add_directly': can_add_directly and is_available,
        'variation_id': display_variation.pk if display_variation else '',
    }


def resolve_exit_popup_add(request, product, variation=None):
    postavke = SiteSettings.load()
    if not postavke.korpa_exit_popup_aktivan:
        return None
    if postavke.korpa_exit_popup_artikal_id != product.pk:
        return None

    percent = _clamp_percent(postavke.korpa_exit_popup_popust)
    resolved_variation = variation

    if product.varijacije.exists():
        if not resolved_variation:
            in_stock = product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id')
            if in_stock.count() == 1:
                resolved_variation = in_stock.first()
            else:
                return None
        elif not resolved_variation.na_stanju:
            return None
    elif not product.na_stanju:
        return None

    prices = exit_popup_prices_for_product(product, percent, resolved_variation)
    result = {
        'variation': resolved_variation,
        'percent': percent,
    }
    if prices['has_discount']:
        result['custom_price'] = prices['snizena']
        result['promo_bazna'] = prices['bazna']
    return result


def dismiss_cart_exit_popup(request):
    request.session[EXIT_POPUP_DISMISSED_KEY] = True
    request.session.modified = True