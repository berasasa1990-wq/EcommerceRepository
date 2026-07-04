from decimal import Decimal

from .models import UpsellOffer

UPSELL_SESSION_KEY = 'upsell_offer_id'
LEGACY_UPSELL_SESSION_KEY = 'pending_upsell_offer_id'


def set_upsell_offer_session(request, offer_id):
    request.session[UPSELL_SESSION_KEY] = offer_id
    request.session.pop(LEGACY_UPSELL_SESSION_KEY, None)
    request.session.modified = True


def clear_upsell_offer_session(request):
    request.session.pop(UPSELL_SESSION_KEY, None)
    request.session.pop(LEGACY_UPSELL_SESSION_KEY, None)
    request.session.modified = True


def get_upsell_offer_id(request):
    offer_id = request.session.get(UPSELL_SESSION_KEY)
    if offer_id:
        return offer_id
    legacy_id = request.session.pop(LEGACY_UPSELL_SESSION_KEY, None)
    if legacy_id:
        request.session[UPSELL_SESSION_KEY] = legacy_id
        request.session.modified = True
        return legacy_id
    return None


def _discounted_price(base_price, offer):
    discounted = base_price
    if offer.popust_postotak:
        discounted = (
            base_price * (Decimal('1') - offer.popust_postotak / Decimal('100'))
        ).quantize(Decimal('0.01'))
    if offer.popust_km:
        discounted = max(Decimal('0'), discounted - offer.popust_km).quantize(Decimal('0.01'))
    return discounted


def build_upsell_offer_context(offer):
    offered_products = []
    for product in offer.ponuda_artikli.filter(aktivan=True):
        original = product.prikazna_cijena
        discounted = _discounted_price(original, offer)

        variations = []
        in_stock_variations = product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'naziv')
        for variation in in_stock_variations:
            variation_original = variation.prikazna_cijena
            variation_discounted = _discounted_price(variation_original, offer)
            variations.append({
                'id': variation.id,
                'naziv': variation.naziv,
                'original_price': variation_original,
                'price': variation_discounted,
            })

        if variations:
            display_orig = min(item['original_price'] for item in variations)
            display_price = min(item['price'] for item in variations)
        else:
            display_orig = original
            display_price = discounted

        offered_products.append({
            'id': product.id,
            'naziv': product.naziv,
            'slika_url': product.slika.url if product.slika else None,
            'original_price': display_orig,
            'price': display_price,
            'has_discount': display_price < display_orig,
            'has_variations': bool(variations),
            'variations': variations,
        })

    if not offered_products:
        return None

    return {
        'id': offer.id,
        'naziv': offer.naziv,
        'naslov_ponude': offer.naslov_ponude,
        'opis_ponude': offer.opis_ponude,
        'prikaz': offer.prikaz,
        'baner_url': offer.baner_slika.url if offer.baner_slika else None,
        'tekst_dugmeta': offer.tekst_dugmeta,
        'popust_postotak': offer.popust_postotak,
        'products': offered_products,
    }


def get_active_upsell_offer(request):
    """Popup ponuda — jednom nakon triggera (dodavanje artikla u korpu)."""
    offer_id = get_upsell_offer_id(request)
    if not offer_id:
        return None
    clear_upsell_offer_session(request)
    try:
        offer = UpsellOffer.objects.prefetch_related('ponuda_artikli').get(
            pk=offer_id,
            aktivan=True,
            prikaz=UpsellOffer.PrikazTip.POPUP,
        )
    except UpsellOffer.DoesNotExist:
        return None
    return build_upsell_offer_context(offer)


def _exclude_cart_products(offer_context, cart):
    if not offer_context or cart is None:
        return offer_context
    cart_product_ids = {item['product_id'] for item in cart}
    products = [
        product for product in offer_context['products']
        if product['id'] not in cart_product_ids
    ]
    if not products:
        return None
    return {**offer_context, 'products': products}


def get_upsell_offers_for_prikaz(prikaz, *, cart=None, exclude_in_cart=False):
    """Aktivne upsell ponude za zadati prikaz (bez triggera)."""
    offers = UpsellOffer.objects.filter(
        aktivan=True,
        prikaz=prikaz,
    ).prefetch_related('ponuda_artikli').order_by('redoslijed', 'id')

    results = []
    for offer in offers:
        context = build_upsell_offer_context(offer)
        if not context:
            continue
        if exclude_in_cart:
            context = _exclude_cart_products(context, cart)
        if context:
            results.append(context)
    return results


def get_cart_banner_upsell_offers(prikaz):
    return get_upsell_offers_for_prikaz(prikaz)


def get_checkout_upsell_offers(cart):
    return get_upsell_offers_for_prikaz(
        UpsellOffer.PrikazTip.CHECKOUT,
        cart=cart,
        exclude_in_cart=True,
    )

# ====================== X+1 Quantity Deal helpers ======================

def get_quantity_deal(product):
    """Vrati aktivni X+1 deal za dati artikal, ako postoji."""
    if not product:
        return None
    from .models import Akcija

    for akcija in Akcija.objects.filter(
        aktivan=True,
        tip=Akcija.Tip.X_PLUS_1,
        artikal=product,
        deal_vrsta__isnull=False,
        popust_postotak__isnull=False,
    ).order_by('redoslijed', '-id'):
        if akcija.jos_traje():
            return akcija
    return UpsellOffer.objects.filter(
        aktivan=True,
        deal_artikal=product,
        deal_vrsta__isnull=False,
        deal_popust__isnull=False,
    ).first()


def _deal_vrsta_value(deal):
    if hasattr(deal, 'deal_vrsta'):
        return deal.deal_vrsta
    return None


def _deal_popust_value(deal):
    if hasattr(deal, 'popust_postotak') and deal.popust_postotak is not None:
        return deal.popust_postotak
    return getattr(deal, 'deal_popust', None)


def parse_deal_vrsta(deal):
    """Vrati (buy, get) iz '2+1' itd."""
    vrsta = _deal_vrsta_value(deal)
    if not deal or not vrsta:
        return None, None
    try:
        buy, get = [int(x) for x in vrsta.split('+')]
        return buy, get
    except Exception:
        return None, None


def _format_deal_pct(popust):
    if popust is None:
        return None
    return int(popust) if popust == int(popust) else popust


def get_deal_cart_nudge(deal, quantity):
    """Crvena poruka u korpi ispod cijene — potakni još jednu količinu."""
    popust = _deal_popust_value(deal)
    if not deal or not _deal_vrsta_value(deal) or popust is None:
        return None
    buy, get = parse_deal_vrsta(deal)
    if not buy or not get:
        return None

    group = buy + get
    remainder = quantity % group
    if remainder == 0:
        return None

    pct = _format_deal_pct(popust)
    until_discount = group - remainder
    if until_discount == 1:
        if pct >= 100:
            return 'Poručite još jedan — sljedeći artikal je GRATIS.'
        return f'Poručite još jedan sa popustom od {pct}%.'
    if until_discount > 1:
        if pct >= 100:
            return f'Poručite još {until_discount} da ostvarite GRATIS artikal.'
        return f'Poručite još {until_discount} da ostvarite popust od {pct}%.'
    return None


def get_deal_message(deal):
    """Vrati tekst poruke za prikaz ispod količine (crveno)."""
    popust = _deal_popust_value(deal)
    if not deal or not _deal_vrsta_value(deal) or popust is None:
        return None
    buy, get = parse_deal_vrsta(deal)
    if not buy or not get:
        return None

    pct = _format_deal_pct(popust)

    if pct >= 100:
        return f"Ako poručite {buy} ova artikla {buy + get}-ći vam je GRATIS."
    return f"Ako poručite {buy} ova artikla {buy + get}-ći vam snižen za {pct}%."


def calculate_deal_breakdown(base_unit_price, quantity, deal):
    """Raspodjela cijena za X+1 deal (puna vs snižena količina)."""
    popust = _deal_popust_value(deal)
    vrsta = _deal_vrsta_value(deal)
    base_unit_price = Decimal(str(base_unit_price))
    quantity = int(quantity or 0)
    original_total = (base_unit_price * quantity).quantize(Decimal('0.01'))

    if not deal or not vrsta or popust is None or quantity <= 0:
        return {
            'deal_total': original_total,
            'original_total': original_total,
            'has_discount': False,
            'full_price_count': quantity,
            'discounted_count': 0,
            'discounted_unit_price': None,
            'full_unit_price': base_unit_price,
            'pct': popust,
            'deal_vrsta': vrsta,
        }

    buy, get = parse_deal_vrsta(deal)
    if not buy or not get:
        return {
            'deal_total': original_total,
            'original_total': original_total,
            'has_discount': False,
            'full_price_count': quantity,
            'discounted_count': 0,
            'discounted_unit_price': None,
            'full_unit_price': base_unit_price,
            'pct': popust,
            'deal_vrsta': vrsta,
        }

    discount_rate = (Decimal('100') - Decimal(str(popust))) / Decimal('100')
    if discount_rate < 0:
        discount_rate = Decimal('0')

    discounted_price = (base_unit_price * discount_rate).quantize(Decimal('0.01'))
    group_size = buy + get
    num_groups = quantity // group_size
    remainder = quantity % group_size

    discounted_count = num_groups * get
    if remainder > buy:
        discounted_count += (remainder - buy)

    full_price_count = quantity - discounted_count
    deal_total = (
        full_price_count * base_unit_price + discounted_count * discounted_price
    ).quantize(Decimal('0.01'))

    return {
        'deal_total': deal_total,
        'original_total': original_total,
        'has_discount': deal_total < original_total,
        'full_price_count': full_price_count,
        'discounted_count': discounted_count,
        'discounted_unit_price': discounted_price if discounted_count else None,
        'full_unit_price': base_unit_price,
        'pct': popust,
        'deal_vrsta': vrsta,
    }


def calculate_deal_adjusted_total(base_unit_price, quantity, deal):
    breakdown = calculate_deal_breakdown(base_unit_price, quantity, deal)
    return breakdown['deal_total'], breakdown['original_total']


def format_deal_order_note(deal_info):
    """Tekst u nazivu stavke narudžbe za račun/email."""
    if not deal_info or not deal_info.get('has_discount'):
        return ''
    vrsta = deal_info.get('deal_vrsta') or ''
    disc_qty = deal_info.get('discounted_count') or 0
    disc_unit = deal_info.get('discounted_unit_price')
    pct = _format_deal_pct(deal_info.get('pct'))
    if not vrsta or not disc_qty or disc_unit is None or pct is None:
        return ''
    return (
        f' ({vrsta}: {disc_qty} kom. sniženo za {pct}%'
        f' - sniženo na {disc_unit} KM)'
    )


def get_deal_info_for_cart_item(item, product):
    """Vrati dict sa informacijama o dealu za prikaz u korpi."""
    deal = get_quantity_deal(product)
    if not deal:
        return None

    base_price = Decimal(item.get('cijena', '0'))
    qty = int(item.get('quantity', 0))
    breakdown = calculate_deal_breakdown(base_price, qty, deal)
    has_deal = breakdown['has_discount']
    message = get_deal_cart_nudge(deal, qty) if not has_deal else None

    return {
        'message': message,
        'nudge': message,
        'deal_total': breakdown['deal_total'],
        'original_total': breakdown['original_total'],
        'has_discount': has_deal,
        'discounted_unit_price': breakdown['discounted_unit_price'],
        'discounted_count': breakdown['discounted_count'],
        'full_unit_price': breakdown['full_unit_price'],
        'pct': breakdown['pct'],
        'deal_vrsta': breakdown['deal_vrsta'],
    }

def get_deal_promo_data(product):
    """Return data for the pulsating promo box on product detail page for X+1 deals."""
    if not product:
        return None
    deal = get_quantity_deal(product)
    popust = _deal_popust_value(deal)
    if not deal or not _deal_vrsta_value(deal) or popust is None:
        return None

    buy, get = parse_deal_vrsta(deal)
    if not buy or not get:
        return None

    base_price = product.prikazna_cijena
    if not base_price:
        return None

    promote_qty = buy + 1
    regular_total = (base_price * promote_qty).quantize(Decimal('0.01'))
    deal_total, _ = calculate_deal_adjusted_total(base_price, promote_qty, deal)

    pct = popust
    is_free = pct >= 100

    # Format with comma for decimal as in region
    def fmt(val):
        s = f"{val:.2f}".replace('.', ',')
        return s

    if is_free:
        promo_text = f"Poruči {promote_qty} ova artikla za samo {fmt(deal_total)} KM (GRATIS!)"
    else:
        promo_text = f"Poruči {promote_qty} ova artikla za samo {fmt(deal_total)} KM"

    regular_str = fmt(regular_total)
    deal_str = fmt(deal_total)
    pct_str = f"{int(pct) if pct == int(pct) else pct}%"

    return {
        'promo_text': promo_text,
        'regular_str': regular_str,
        'deal_str': deal_str,
        'pct_str': pct_str,
        'is_free': is_free,
        'promote_qty': promote_qty,
        'buy': buy,
        'get': get,
    }
