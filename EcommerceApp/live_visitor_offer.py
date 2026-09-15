import secrets
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .cart_tracking import get_cart_session_key
from .models import Coupon, LiveVisitorOffer, Order

# Legacy — stari pozivi su nudili 10%; novi nude besplatnu dostavu.
REGISTRATION_COUPON_NAME = 'Registracijski popust (uživo)'
SESSION_REG_INVITE_KEY = 'live_reg_invite_pending'
LOYALTY_POPUP_WELCOME_PERCENT = Decimal('10')
SESSION_FREE_SHIPPING_KEY = 'cart_free_shipping_first'
# Welcome reg popup — uključuje se u SiteSettings
# AI dwell: odmah na ulasku na artikal → flash % popust (uključuje se u SiteSettings)
SESSION_DWELL_FLASH_KEY = 'product_dwell_flash'  # {pid: {percent, expires_ts, base}}


def _clamp_percent(value):
    """None/prazno/0 = bez popusta; max 50%."""
    try:
        if value is None or value == '':
            percent = Decimal('0')
        else:
            percent = Decimal(str(value))
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


def _flash_deals(request):
    raw = request.session.get(SESSION_DWELL_FLASH_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _save_flash_deals(request, deals):
    request.session[SESSION_DWELL_FLASH_KEY] = deals
    request.session.modified = True


def get_active_dwell_flash(request, product_id):
    """
    Aktivna flash cijena za artikal (bez popupa).
    Vraća {percent, expires_ts, remaining_seconds, base, sale} ili None.
    """
    if not request or not product_id:
        return None
    try:
        pid = str(int(product_id))
    except (TypeError, ValueError):
        return None
    deals = _flash_deals(request)
    deal = deals.get(pid)
    if not isinstance(deal, dict):
        return None
    try:
        expires = float(deal.get('expires_ts') or 0)
    except (TypeError, ValueError):
        expires = 0
    now = timezone.now().timestamp()
    remaining = int(expires - now)
    if remaining <= 0:
        deals.pop(pid, None)
        _save_flash_deals(request, deals)
        return None
    try:
        percent = Decimal(str(deal.get('percent') or 0))
    except (InvalidOperation, TypeError, ValueError):
        percent = Decimal('0')
    if percent <= 0:
        return None
    base = deal.get('base')
    sale = deal.get('sale')
    try:
        pct_f = float(percent)
        pct_out = int(pct_f) if pct_f == int(pct_f) else pct_f
    except (TypeError, ValueError):
        pct_out = str(percent)
    return {
        'product_id': int(pid),
        'percent': percent,
        'percent_display': pct_out,
        'expires_ts': expires,
        'remaining_seconds': remaining,
        'base': base,
        'sale': sale,
    }


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


def mark_loyalty_popup_registration_pending(request):
    """Loyalty Club popup: jednokratni 10% na prvu narudžbu."""
    if request is None:
        return
    request.session[SESSION_REG_INVITE_KEY] = (
        f'percent:{LOYALTY_POPUP_WELCOME_PERCENT}'
    )
    request.session.modified = True


def _pending_invite_percent(pending):
    if not isinstance(pending, str) or not pending.startswith('percent:'):
        return None
    try:
        percent = _clamp_percent(pending.split(':', 1)[1])
    except Exception:
        percent = LOYALTY_POPUP_WELCOME_PERCENT
    if percent <= 0:
        return LOYALTY_POPUP_WELCOME_PERCENT
    return percent


def _create_registration_reward_coupon(user, percent):
    existing = get_active_registration_reward_coupon(user)
    if existing:
        return existing
    code = f'REG{secrets.token_hex(3).upper()[:6]}'
    while Coupon.objects.filter(kod=code).exists():
        code = f'REG{secrets.token_hex(3).upper()[:6]}'
    return Coupon.objects.create(
        kod=code,
        naziv=REGISTRATION_COUPON_NAME,
        postotak=percent,
        vlasnik=user,
        aktivan=True,
        # Ne loyalty-kupon: 10% na prvu narudžbu, ne samo na artikle bez akcije.
        automatski=False,
    )


def claim_registration_invite_reward(request, user):
    """
    Nakon registracije: % kupon ili besplatna dostava na prvu narudžbu.
    """
    if not user or not user.pk:
        return None

    session_key = get_cart_session_key(request)
    pending = request.session.get(SESSION_REG_INVITE_KEY)
    pending_percent = _pending_invite_percent(pending)

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
            offer.save(update_fields=[
                'user', 'kod_aktiviran', 'show_popup', 'azurirano',
            ])
        request.session.pop(SESSION_REG_INVITE_KEY, None)
        return None

    percent = Decimal('0')
    free_ship = True
    if pending_percent:
        percent = pending_percent
        free_ship = False
    elif offer:
        percent = _clamp_percent(offer.discount_percent or 0)
        free_ship = bool(offer.besplatna_dostava) and percent <= 0

    if offer:
        offer.user = user
        offer.kod_aktiviran = True
        offer.show_popup = False
        if percent > 0:
            offer.discount_percent = percent
            offer.besplatna_dostava = False
        offer.save(update_fields=[
            'user', 'kod_aktiviran', 'show_popup',
            'discount_percent', 'besplatna_dostava', 'azurirano',
        ])
    else:
        sk = session_key or f'reg-user-{user.pk}'
        LiveVisitorOffer.objects.create(
            session_key=sk,
            user=user,
            tip=LiveVisitorOffer.Tip.REGISTRACIJA,
            discount_percent=percent,
            besplatna_dostava=free_ship,
            kod_aktiviran=True,
            show_popup=False,
            added_to_cart=False,
        )

    request.session.pop(SESSION_REG_INVITE_KEY, None)
    request.session.modified = True

    if percent > 0:
        coupon = _create_registration_reward_coupon(user, percent)
        pct = coupon.postotak
        percent_label = (
            str(int(pct)) if pct == pct.to_integral_value() else str(pct)
        )
        return {
            'percent': percent_label,
            'type': 'registration',
            'coupon': coupon.kod,
        }

    set_session_free_shipping(request, True)
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
