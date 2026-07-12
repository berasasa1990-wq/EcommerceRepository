import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from .cart_tracking import get_cart_session_key
from .models import Coupon, LiveVisitorOffer, Order, Product, ProductVariation

OFFER_TIMER_MINUTES = 9
# Legacy — stari pozivi su nudili 10%; novi nude besplatnu dostavu.
REGISTRATION_INVITE_DISCOUNT = Decimal('0')
REGISTRATION_COUPON_NAME = 'Registracijski popust (uživo)'
REGISTRATION_FREE_SHIPPING_NAME = 'Besplatna dostava (registracija uživo)'
SESSION_REG_INVITE_KEY = 'live_reg_invite_pending'
SESSION_FREE_SHIPPING_KEY = 'cart_free_shipping_first'


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


def _discounted_price(base_price, percent):
    base_price = Decimal(str(base_price or 0))
    percent = _clamp_percent(percent)
    if percent <= 0:
        return base_price.quantize(Decimal('0.01'))
    return (base_price * (Decimal('1') - percent / Decimal('100'))).quantize(Decimal('0.01'))


def _generate_activation_code():
    return f'PONUDA-{secrets.token_hex(3).upper()[:6]}'


def _offer_lookup_q(request):
    session_key = get_cart_session_key(request)
    clauses = Q()
    if session_key:
        clauses |= Q(session_key=session_key)
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        clauses |= Q(user=user)
    return clauses


def _active_offer_filter():
    return (
        Q(tip=LiveVisitorOffer.Tip.NARUDZBA, kod_aktiviran=False)
        | Q(tip=LiveVisitorOffer.Tip.ARTIKAL, added_to_cart=False)
        | Q(tip=LiveVisitorOffer.Tip.REGISTRACIJA)
    )


def _upsert_live_visitor_offer(session_key, defaults, *, target_user=None):
    if target_user and not isinstance(target_user, User):
        target_user = None

    defaults = dict(defaults)
    defaults['session_key'] = session_key

    if target_user:
        defaults['user'] = target_user
        offer = LiveVisitorOffer.objects.filter(user=target_user).first()
        if not offer:
            offer = LiveVisitorOffer.objects.filter(session_key=session_key).first()
        if offer:
            for field, value in defaults.items():
                setattr(offer, field, value)
            offer.save(update_fields=list(defaults.keys()) + ['azurirano'])
        else:
            LiveVisitorOffer.objects.filter(session_key=session_key).delete()
            offer = LiveVisitorOffer.objects.create(**defaults)
    else:
        defaults['user'] = None
        offer = LiveVisitorOffer.objects.filter(
            session_key=session_key,
            user__isnull=True,
        ).first()
        if offer:
            for field, value in defaults.items():
                setattr(offer, field, value)
            offer.save(update_fields=list(defaults.keys()) + ['azurirano'])
        else:
            LiveVisitorOffer.objects.filter(session_key=session_key, user__isnull=True).delete()
            offer = LiveVisitorOffer.objects.create(**defaults)
    return offer


def send_live_visitor_offer(
    session_key,
    *,
    product_id=None,
    discount_percent=0,
    free_shipping=False,
    staff_user=None,
    target_user=None,
):
    if not session_key:
        raise ValueError('Sesija posjetioca nije pronađena.')

    percent = _clamp_percent(discount_percent)
    free_shipping = bool(free_shipping)
    product = None
    if product_id:
        product = Product.objects.filter(pk=product_id, aktivan=True).first()
        if not product:
            raise ValueError('Artikal nije pronađen ili nije aktivan.')

    if product:
        tip = LiveVisitorOffer.Tip.ARTIKAL
        code = ''
    elif percent > 0 or free_shipping:
        tip = LiveVisitorOffer.Tip.NARUDZBA
        code = _generate_activation_code()
    else:
        raise ValueError('Unesite popust %, besplatnu dostavu ili odaberite artikal.')

    defaults = {
        'tip': tip,
        'product': product,
        'discount_percent': percent,
        'besplatna_dostava': free_shipping,
        'aktivacioni_kod': code,
        'kod_aktiviran': False,
        'show_popup': True,
        'added_to_cart': False,
        'poslao': staff_user if isinstance(staff_user, User) else None,
    }
    return _upsert_live_visitor_offer(session_key, defaults, target_user=target_user)


def send_live_visitor_registration_invite(session_key, *, staff_user=None, target_user=None):
    """Pošalji gostu popup poziv na registraciju (+ besplatna dostava na prvu narudžbu)."""
    if not session_key:
        raise ValueError('Sesija posjetioca nije pronađena.')
    if target_user and getattr(target_user, 'is_authenticated', False):
        raise ValueError('Kupac je već registrovan.')

    defaults = {
        'tip': LiveVisitorOffer.Tip.REGISTRACIJA,
        'product': None,
        'discount_percent': Decimal('0'),
        'besplatna_dostava': True,
        'aktivacioni_kod': '',
        'kod_aktiviran': False,
        'show_popup': True,
        'added_to_cart': False,
        'poslao': staff_user if isinstance(staff_user, User) else None,
    }
    return _upsert_live_visitor_offer(session_key, defaults, target_user=None)


def set_session_free_shipping(request, active=True):
    if active:
        request.session[SESSION_FREE_SHIPPING_KEY] = '1'
    else:
        request.session.pop(SESSION_FREE_SHIPPING_KEY, None)
    request.session.modified = True


def session_has_free_shipping(request):
    return bool(request and request.session.get(SESSION_FREE_SHIPPING_KEY))


def user_has_first_order_free_shipping(user):
    """Registrovan kupac s prihvaćenom besplatnom dostavom, još bez narudžbe."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if Order.objects.filter(korisnik=user).exists():
        return False
    return LiveVisitorOffer.objects.filter(
        user=user,
        besplatna_dostava=True,
        kod_aktiviran=True,
    ).exists()


def has_free_shipping_reward(request, user=None):
    user = user if user is not None else getattr(request, 'user', None)
    if session_has_free_shipping(request):
        if not user or not getattr(user, 'is_authenticated', False):
            return True
        # Registrovan: samo dok nema nijednu narudžbu
        return not Order.objects.filter(korisnik=user).exists()
    return user_has_first_order_free_shipping(user)


def clear_free_shipping_reward(request, user=None):
    if request is not None:
        set_session_free_shipping(request, False)
    user = user if user is not None else (getattr(request, 'user', None) if request else None)
    if user and getattr(user, 'is_authenticated', False):
        # Ostavi kod_aktiviran=True (iskorišteno), ali nakon narudžbe
        # user_has_first_order_free_shipping više ne prolazi jer postoji Order.
        pass


def mark_registration_invite_pending(request, offer):
    """Zapamti u sesiji da je posjetilac dobio poziv (besplatna dostava)."""
    if not offer or offer.tip != LiveVisitorOffer.Tip.REGISTRACIJA:
        return
    request.session[SESSION_REG_INVITE_KEY] = 'free_shipping'
    request.session.modified = True


def claim_registration_invite_reward(request, user):
    """
    Nakon registracije: besplatna dostava na prvu narudžbu (jednokratno).
    """
    if not user or not user.pk:
        return None

    session_key = get_cart_session_key(request)
    pending = request.session.get(SESSION_REG_INVITE_KEY)

    offer = None
    if session_key:
        offer = (
            LiveVisitorOffer.objects
            .filter(
                session_key=session_key,
                tip=LiveVisitorOffer.Tip.REGISTRACIJA,
                kod_aktiviran=False,
            )
            .order_by('-azurirano')
            .first()
        )

    if not offer and not pending:
        return None

    # Već iskoristio ranije (ima narudžbu) — ne daj ponovo
    if Order.objects.filter(korisnik=user).exists():
        if offer:
            offer.user = user
            offer.kod_aktiviran = True
            offer.show_popup = False
            offer.besplatna_dostava = True
            offer.save(update_fields=[
                'user', 'kod_aktiviran', 'show_popup', 'besplatna_dostava', 'azurirano',
            ])
        request.session.pop(SESSION_REG_INVITE_KEY, None)
        return None

    if offer:
        offer.user = user
        offer.discount_percent = Decimal('0')
        offer.besplatna_dostava = True
        offer.kod_aktiviran = True
        offer.show_popup = False
        offer.save(update_fields=[
            'user', 'discount_percent', 'besplatna_dostava',
            'kod_aktiviran', 'show_popup', 'azurirano',
        ])
    else:
        # Pending iz sesije bez aktivne ponude u bazi
        sk = session_key or f'reg-user-{user.pk}'
        LiveVisitorOffer.objects.create(
            session_key=sk,
            user=user,
            tip=LiveVisitorOffer.Tip.REGISTRACIJA,
            discount_percent=Decimal('0'),
            besplatna_dostava=True,
            kod_aktiviran=True,
            show_popup=False,
            added_to_cart=False,
        )

    set_session_free_shipping(request, True)
    request.session.pop(SESSION_REG_INVITE_KEY, None)
    request.session.modified = True
    return {'free_shipping': True, 'type': 'registration'}


def registration_reward_coupon_code(user):
    """Legacy: stari registracijski % kupon (ako još postoji aktivan)."""
    coupon = get_active_registration_reward_coupon(user)
    return coupon.kod if coupon else ''


def get_active_registration_reward_coupon(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return (
        Coupon.objects
        .filter(
            vlasnik=user,
            naziv=REGISTRATION_COUPON_NAME,
            aktivan=True,
        )
        .order_by('-kreiran')
        .first()
    )


def consume_registration_reward(user):
    """Nakon narudžbe — stari % kupon više ne vrijedi."""
    if not user or not getattr(user, 'is_authenticated', False):
        return
    Coupon.objects.filter(
        vlasnik=user,
        naziv=REGISTRATION_COUPON_NAME,
        aktivan=True,
    ).update(aktivan=False)


def get_active_live_visitor_offer(request):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return None
    # Registrovanim korisnicima ne prikazuj poziv na registraciju
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        LiveVisitorOffer.objects.filter(
            lookup,
            tip=LiveVisitorOffer.Tip.REGISTRACIJA,
            show_popup=True,
        ).update(show_popup=False)
    from .browse_interest_offer import AUTO_BROWSE_CODE

    return (
        LiveVisitorOffer.objects.filter(
            lookup,
            show_popup=True,
        )
        .filter(_active_offer_filter())
        .exclude(aktivacioni_kod=AUTO_BROWSE_CODE)
        .exclude(aktivacioni_kod__startswith=f'{AUTO_BROWSE_CODE}-')
        .select_related('product')
        .order_by('-azurirano')
        .first()
    )


def _offer_timer_seconds(offer):
    expires_at = offer.azurirano + timedelta(minutes=OFFER_TIMER_MINUTES)
    remaining = int((expires_at - timezone.now()).total_seconds())
    return max(0, remaining)


def _percent_display(discount):
    if not discount or discount <= 0:
        return None
    return int(discount) if discount == int(discount) else float(discount)


def _build_order_offer_payload(offer):
    discount = offer.discount_percent or Decimal('0')
    pct_display = _percent_display(discount)
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    if not free_shipping and (not pct_display or not offer.aktivacioni_kod):
        return None
    if free_shipping and not pct_display:
        return {
            'offer_type': 'free_shipping',
            'offer_id': offer.pk,
            'offer_version': int(offer.azurirano.timestamp()),
            'free_shipping': True,
            'title': 'Besplatna dostava na prvu kupovinu',
            'message': (
                'Prihvatite ponudu i na prvu narudžbu dostava vam je besplatna. '
                'Vrijedi samo jednom — za prvu kupovinu.'
            ),
            'activation_code': offer.aktivacioni_kod or '',
            'timer_seconds': _offer_timer_seconds(offer),
            'timer_minutes': OFFER_TIMER_MINUTES,
            'activate_url': '/ponuda/aktiviraj/',
            'dismiss_url': '/ponuda/zatvori/',
            'cta_label': 'Prihvati besplatnu dostavu',
        }
    return {
        'offer_type': 'order',
        'offer_id': offer.pk,
        'offer_version': int(offer.azurirano.timestamp()),
        'discount_percent': pct_display,
        'free_shipping': free_shipping,
        'activation_code': offer.aktivacioni_kod,
        'timer_seconds': _offer_timer_seconds(offer),
        'timer_minutes': OFFER_TIMER_MINUTES,
        'activate_url': '/ponuda/aktiviraj/',
        'dismiss_url': '/ponuda/zatvori/',
    }


def _build_product_offer_payload(offer):
    product = offer.product
    if not product:
        return None
    discount = offer.discount_percent or Decimal('0')
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    in_stock_variations = list(
        product.varijacije.filter(na_stanju=True).order_by('redoslijed', 'id'),
    )

    if not in_stock_variations and not product.na_stanju:
        return None

    variations = []
    for variation in in_stock_variations:
        base_price = variation.prikazna_cijena
        final_price = _discounted_price(base_price, discount)
        variations.append({
            'id': variation.pk,
            'naziv': variation.naziv,
            'base_price': str(base_price),
            'final_price': str(final_price),
            'has_discount': discount > 0 and final_price < base_price,
        })

    if variations:
        display_base = variations[0]['base_price']
        display_final = variations[0]['final_price']
        has_discount = variations[0]['has_discount']
    else:
        display_base = str(product.prikazna_cijena)
        display_final = str(_discounted_price(product.prikazna_cijena, discount))
        has_discount = discount > 0 and Decimal(display_final) < Decimal(display_base)

    return {
        'offer_type': 'product',
        'offer_id': offer.pk,
        'offer_version': int(offer.azurirano.timestamp()),
        'product_id': product.pk,
        'product_name': product.naziv,
        'product_url': product.get_absolute_url(),
        'image_url': product.prikazna_slika.url if product.prikazna_slika else '',
        'discount_percent': _percent_display(discount),
        'has_discount': has_discount,
        'free_shipping': free_shipping,
        'has_variations': bool(variations),
        'variations': variations,
        'display_base_price': display_base,
        'display_final_price': display_final,
        'timer_seconds': _offer_timer_seconds(offer),
        'timer_minutes': OFFER_TIMER_MINUTES,
        'add_url': '/ponuda/dodaj/',
        'dismiss_url': '/ponuda/zatvori/',
    }


def _build_registration_offer_payload(offer):
    return {
        'offer_type': 'registration',
        'offer_id': offer.pk,
        'offer_version': int(offer.azurirano.timestamp()),
        'discount_percent': None,
        'free_shipping': True,
        'title': 'Registrujte se i ostvarite besplatnu dostavu',
        'message': (
            'Nakon registracije na prvu narudžbu imate besplatnu dostavu. '
            'Pogodnost vrijedi samo jednom — za prvu kupovinu.'
        ),
        'benefits': [
            'Besplatna dostava na prvu narudžbu',
            'Automatski se primjenjuje u korpi',
            'Vrijedi samo jednom — nakon porudžbe prestaje',
        ],
        'cta_label': 'Registruj se i uzmi besplatnu dostavu',
        'register_url': '/registracija/',
        'timer_seconds': _offer_timer_seconds(offer),
        'timer_minutes': OFFER_TIMER_MINUTES,
        'dismiss_url': '/ponuda/zatvori/',
    }


def _build_offer_payload(offer):
    if offer.tip == LiveVisitorOffer.Tip.REGISTRACIJA:
        return _build_registration_offer_payload(offer)
    if offer.tip == LiveVisitorOffer.Tip.NARUDZBA:
        return _build_order_offer_payload(offer)
    return _build_product_offer_payload(offer)


def build_live_visitor_offer_context(request):
    offer = get_active_live_visitor_offer(request)
    if not offer:
        return None
    payload = _build_offer_payload(offer)
    if not payload:
        return None
    mark_registration_invite_pending(request, offer)
    payload['offer'] = offer
    if offer.product_id:
        payload['product'] = offer.product
    return payload


def poll_live_visitor_offer(request):
    offer = get_active_live_visitor_offer(request)
    if offer:
        mark_registration_invite_pending(request, offer)
        return _build_offer_payload(offer)
    # Fallback: personalizovana ponuda na osnovu gledanja (2+ pregleda / top kategorija)
    try:
        from .browse_interest_offer import poll_browse_interest_offer

        return poll_browse_interest_offer(request)
    except Exception:
        return None


def activate_live_visitor_offer_code(request, cart):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return False, 'Ponuda više nije dostupna.'

    offer = LiveVisitorOffer.objects.filter(
        lookup,
        tip=LiveVisitorOffer.Tip.NARUDZBA,
        show_popup=True,
        kod_aktiviran=False,
    ).order_by('-azurirano').first()
    if not offer:
        return False, 'Ponuda više nije dostupna.'

    percent = offer.discount_percent or Decimal('0')
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    if percent <= 0 and not free_shipping:
        return False, 'Ponuda nema popusta ni besplatne dostave.'

    messages = []
    if percent > 0:
        cart.set_recovery_discount(percent)
        pct = _percent_display(percent)
        messages.append(f'{pct}% popusta na cijelu narudžbu')

    assigned_user = False
    if free_shipping:
        set_session_free_shipping(request, True)
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            offer.user = user
            assigned_user = True
        messages.append('besplatna dostava na prvu kupovinu')

    offer.kod_aktiviran = True
    offer.show_popup = False
    update_fields = ['kod_aktiviran', 'show_popup', 'azurirano']
    if assigned_user:
        update_fields.append('user')
    offer.save(update_fields=update_fields)

    if free_shipping and percent > 0:
        msg = (
            f'Šta god da poručite, {messages[0]} — i {messages[1]}. '
            f'Besplatna dostava vrijedi samo za prvu narudžbu.'
        )
    elif free_shipping:
        msg = (
            'Besplatna dostava je aktivirana na prvu kupovinu. '
            'Vrijedi samo jednom — za prvu narudžbu.'
        )
    else:
        pct = _percent_display(percent)
        msg = f'Šta god da poručite, {pct}% vam je sniženo na cijelu narudžbu.'

    return True, {
        'percent': _percent_display(percent) if percent > 0 else None,
        'free_shipping': free_shipping,
        'message': msg,
    }


def apply_live_visitor_offer(request, cart):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return False, 'Ponuda više nije dostupna.'

    offer = LiveVisitorOffer.objects.filter(
        lookup,
        tip=LiveVisitorOffer.Tip.ARTIKAL,
        show_popup=True,
        added_to_cart=False,
    ).select_related('product').order_by('-azurirano').first()
    if not offer or not offer.product:
        return False, 'Ponuda više nije dostupna.'

    product = offer.product
    if not product.aktivan:
        return False, 'Artikal više nije dostupan.'

    in_stock_variations = product.varijacije.filter(na_stanju=True)
    variation = None
    var_id = (request.POST.get('variation_id') or '').strip()
    if var_id:
        try:
            variation = in_stock_variations.get(pk=int(var_id))
        except (ProductVariation.DoesNotExist, ValueError):
            return False, 'Izaberite ispravnu varijaciju.'
    elif in_stock_variations.exists():
        return False, 'Izaberite varijaciju.'

    if variation and not variation.na_stanju:
        return False, 'Varijacija nije na stanju.'
    if not variation and not product.na_stanju:
        return False, 'Artikal nije na stanju.'

    discount = offer.discount_percent or Decimal('0')
    free_shipping = bool(getattr(offer, 'besplatna_dostava', False))
    base_price = variation.prikazna_cijena if variation else product.prikazna_cijena
    if discount > 0:
        final_price = _discounted_price(base_price, discount)
        cart.add(
            product,
            variation=variation,
            quantity=1,
            custom_price=final_price,
            promo_bazna=base_price,
        )
    else:
        cart.add(product, variation=variation, quantity=1)

    assigned_user = False
    if free_shipping:
        set_session_free_shipping(request, True)
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            offer.user = user
            assigned_user = True

    offer.added_to_cart = True
    if free_shipping:
        offer.kod_aktiviran = True
    offer.show_popup = False
    update_fields = ['added_to_cart', 'show_popup', 'azurirano']
    if free_shipping:
        update_fields.append('kod_aktiviran')
    if assigned_user:
        update_fields.append('user')
    offer.save(update_fields=update_fields)

    label = f'{product.naziv}' + (f' — {variation.naziv}' if variation else '')
    parts = []
    if discount > 0:
        pct = _percent_display(discount)
        parts.append(f'popustom od {pct}%')
    if free_shipping:
        parts.append('besplatnom dostavom na prvu kupovinu')
    if parts:
        return True, f'"{label}" je dodato u korpu s {" i ".join(parts)}.'
    return True, f'"{label}" je dodato u korpu.'


def dismiss_live_visitor_offer(request):
    lookup = _offer_lookup_q(request)
    if not lookup:
        return
    LiveVisitorOffer.objects.filter(
        lookup,
        show_popup=True,
    ).update(show_popup=False)
