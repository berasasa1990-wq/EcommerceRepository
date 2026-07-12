"""
Poslednji minut ponuda — samo kad kupac hoće da izađe sa sajta (exit intent).

Artikal se bira pametno:
1) skoro dodao u korpu (kursor na dugmetu, nije kliknuo)
2) #1 sell preporuka po gledanju
3) fallback: SiteSettings.korpa_exit_popup_artikal
"""

from decimal import Decimal, InvalidOperation

from .almost_cart import top_almost_cart_product_id
from .cart_tracking import get_cart_session_key
from .models import LiveVisitor, Product, SiteSettings, _izracunaj_akcijsku_od_postotka

EXIT_POPUP_DISMISSED_KEY = 'cart_exit_popup_dismissed'
EXIT_PRODUCT_SESSION_KEY = 'cart_exit_resolved_product_id'
# Exit podsjetnik kad ima artikle u korpi
CART_ABANDON_DISMISSED_KEY = 'cart_abandon_exit_dismissed'


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


def _product_usable(product):
    if not product or not product.aktivan:
        return False
    if product.varijacije.exists():
        return product.varijacije.filter(na_stanju=True).exists()
    return bool(product.na_stanju)


def resolve_exit_product(request):
    """Odaberi artikal za Poslednji minut (intent > gledanje > admin default)."""
    session_key = get_cart_session_key(request)
    visitor = None
    if session_key:
        visitor = LiveVisitor.objects.filter(session_key=session_key).first()

    # 1) Skoro korpa
    if visitor:
        almost_id = top_almost_cart_product_id(visitor)
        if almost_id:
            product = Product.objects.filter(pk=almost_id, aktivan=True).first()
            if _product_usable(product):
                return product, 'almost_cart'

        # 2) Sell preporuka
        try:
            from .browse_interest_offer import build_sell_recommendations

            recs = build_sell_recommendations(visitor, limit=1)
            if recs:
                product = Product.objects.filter(pk=recs[0]['product_id'], aktivan=True).first()
                if _product_usable(product):
                    return product, 'browse'
        except Exception:
            pass

        # 3) Najgledaniji artikal u sesiji
        products = getattr(visitor, 'pregledani_proizvodi', None) or []
        best = None
        best_views = 0
        for item in products:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get('id') or 0)
                views = int(item.get('views') or 1)
            except (TypeError, ValueError):
                continue
            if pid and views >= best_views:
                best_views = views
                best = pid
        if best:
            product = Product.objects.filter(pk=best, aktivan=True).first()
            if _product_usable(product):
                return product, 'viewed'

    # 4) Admin fallback
    postavke = SiteSettings.load()
    product = postavke.korpa_exit_popup_artikal
    if _product_usable(product):
        return product, 'settings'
    return None, ''


def get_cart_exit_popup_context(request, cart):
    postavke = SiteSettings.load()
    if not postavke.korpa_exit_popup_aktivan:
        return None
    if request.session.get(EXIT_POPUP_DISMISSED_KEY):
        return None

    product, source = resolve_exit_product(request)
    if not product:
        return None

    # Zapamti koji artikal je u ovoj sesiji za exit add
    request.session[EXIT_PRODUCT_SESSION_KEY] = product.pk
    request.session.modified = True

    percent = _clamp_percent(postavke.korpa_exit_popup_popust)
    if percent <= 0:
        percent = Decimal('10')

    in_stock_variations = list(
        product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id'),
    )
    display_variation = in_stock_variations[0] if len(in_stock_variations) == 1 else None
    prices = exit_popup_prices_for_product(product, percent, display_variation)
    usteda = None
    if prices.get('has_discount'):
        try:
            usteda = (Decimal(str(prices['bazna'])) - Decimal(str(prices['snizena']))).quantize(
                Decimal('0.01')
            )
            if usteda <= 0:
                usteda = None
        except Exception:
            usteda = None

    if product.varijacije.exists():
        can_add_directly = len(in_stock_variations) == 1
        is_available = bool(in_stock_variations)
    else:
        can_add_directly = True
        is_available = product.na_stanju

    pct_label = None
    if percent > 0:
        pct_label = int(percent) if percent == int(percent) else float(percent)

    return {
        'product': product,
        'discount_percent': percent if percent > 0 else None,
        'discount_percent_label': pct_label,
        'product_prices': prices,
        'usteda': usteda,
        'can_add_directly': can_add_directly and is_available,
        'variation_id': display_variation.pk if display_variation else '',
        'source': source,
        'exit_only': True,
    }


def resolve_exit_popup_add(request, product, variation=None):
    postavke = SiteSettings.load()
    if not postavke.korpa_exit_popup_aktivan:
        return None

    allowed_id = request.session.get(EXIT_PRODUCT_SESSION_KEY)
    settings_id = postavke.korpa_exit_popup_artikal_id
    if allowed_id and product.pk != int(allowed_id):
        # Dozvoli i admin default artikal
        if not settings_id or product.pk != settings_id:
            return None
    elif not allowed_id and settings_id and product.pk != settings_id:
        return None

    percent = _clamp_percent(postavke.korpa_exit_popup_popust)
    if percent <= 0:
        percent = Decimal('10')
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


def dismiss_cart_abandon_exit(request):
    request.session[CART_ABANDON_DISMISSED_KEY] = True
    request.session.modified = True


def get_cart_abandon_exit_context(request, cart):
    """
    Exit intent: kupac ima artikle u korpi → podsjetnik da završi narudžbu.
    Ne na /korpa/ i /narudzba/.
    """
    if cart is None:
        return None
    try:
        count = len(cart)
    except Exception:
        count = 0
    if count <= 0:
        return None
    if request.session.get(CART_ABANDON_DISMISSED_KEY):
        return None

    path = (getattr(request, 'path', '') or '')
    if path.startswith('/korpa') or path.startswith('/narudzba'):
        return None
    if path.startswith('/nalog/') or path.startswith('/admin'):
        return None

    # Preview stavki (max 3) — iz session cart dict (brzo, bez full __iter__)
    preview = []
    try:
        raw_items = list((cart.cart or {}).values()) if hasattr(cart, 'cart') else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            preview.append({
                'naziv': item.get('product_naziv') or item.get('naziv') or 'Artikal',
                'varijacija': item.get('varijacija_naziv') or '',
                'quantity': int(item.get('quantity') or 1),
                'cijena': item.get('cijena'),
                'slika': item.get('slika') or '',
            })
            if len(preview) >= 3:
                break
    except Exception:
        preview = []

    try:
        total = cart.ukupno
    except Exception:
        total = None

    item_count = cart.item_count if hasattr(cart, 'item_count') else len(preview)
    qty_total = count

    postavke = SiteSettings.load()
    naslov = (postavke.korpa_exit_popup_naslov or '').strip() or 'Imate artikle u korpi'
    tekst = (postavke.korpa_exit_popup_tekst or '').strip() or (
        'Prije nego odete — odabrani artikli još čekaju. Završite narudžbu sada.'
    )

    return {
        'naslov': naslov,
        'tekst': tekst,
        'item_count': item_count,
        'qty_total': qty_total,
        'preview': preview,
        'total': total,
        'cart_url': '/korpa/',
        'checkout_url': '/narudzba/',
    }
