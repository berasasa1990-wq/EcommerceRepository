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