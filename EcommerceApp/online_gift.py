"""Serverska pravila za online nagrade i Sretni Greb-Greb."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from secrets import randbelow

from django.db import IntegrityError, transaction
from django.utils import timezone


from .models import OnlineGiftCampaign, OnlineGiftClaim, ScratchPrize, SiteSettings


SESSION_REWARD_KEY = 'online_gift_reward'
# legacy session keys from greb/wheel
_LEGACY_KEYS = (
    'greb_greb_reward',
    'prize_wheel_reward',
    'greb_greb_played',
    'prize_wheel_spun',
)

SCRATCH_CAMPAIGN_NAME = 'SRETNI GREB-GREB'
SCRATCH_SESSION_KEY = 'sretni_greb_greb_claim'
SCRATCH_PENDING_ORDERS_KEY = 'sretni_greb_greb_pending_orders'
SCRATCH_PRIZES = (
    ('percent_5', 35, '5% popusta', 'percent', Decimal('5'), Decimal('0')),
    ('shipping', 25, 'Besplatna dostava iznad 80 KM', 'shipping', Decimal('0'), Decimal('80')),
    ('gift', 15, 'Poklon uz kupovinu iznad 150 KM', 'gift', Decimal('0'), Decimal('150')),
    ('percent_10', 12, '10% popusta iznad 100 KM', 'percent', Decimal('10'), Decimal('100')),
    ('percent_15', 5, '15% popusta iznad 150 KM', 'percent', Decimal('15'), Decimal('150')),
    ('percent_20', 1, '20% popusta iznad 200 KM', 'percent', Decimal('20'), Decimal('200')),
    ('none', 7, 'Više sreće sljedeći put', 'none', Decimal('0'), Decimal('0')),
)


def _available_scratch_prizes(campaign):
    """Samo eksplicitno aktivne nagrade iz Admina smiju pokrenuti igru."""
    prizes = list(campaign.scratch_prizes.filter(active=True, weight__gt=0).select_related('product'))
    return [
        (prize.code, prize.weight, prize.label, prize.kind, prize.discount_percent,
         prize.minimum, prize.product_id)
        for prize in prizes if prize.weight > 0
    ]


def _scratch_prize_for_claim(claim):
    prize = ScratchPrize.objects.filter(
        campaign=claim.campaign, code=claim.scratch_prize_code,
    ).select_related('product').first()
    if prize:
        return (prize.code, prize.weight, prize.label, prize.kind, prize.discount_percent,
                prize.minimum, prize.product_id)
    legacy = next((item for item in SCRATCH_PRIZES if item[0] == claim.scratch_prize_code), None)
    return (*legacy, None) if legacy else None


def _scratch_campaign():
    campaign, _ = OnlineGiftCampaign.objects.get_or_create(
        naziv=SCRATCH_CAMPAIGN_NAME,
        defaults={'aktivan': True, 'naslov': SCRATCH_CAMPAIGN_NAME, 'automatic': False,
                  'poruka': 'Jedna prilika sedmično.'},
    )
    return campaign


def _pending_scratch_order_ids(request):
    raw = request.session.get(SCRATCH_PENDING_ORDERS_KEY, [])
    if not isinstance(raw, list):
        return []
    return [value for value in raw if isinstance(value, int) and value > 0]


def grant_scratch_chance(request, order):
    """Svaka uspješna webshop narudžba dodaje jednu priliku u red sesije."""
    if not order or order.izvor != order.Izvor.WEBSHOP:
        return
    campaign = _scratch_campaign()
    # Ako je kampanja ugašena ili Admin nema aktivnu nagradu, ne spremamo
    # priliku koja bi kasnije mogla prikazati Greb-Greb popup.
    if not campaign.aktivan or not _available_scratch_prizes(campaign):
        return
    pending = _pending_scratch_order_ids(request)
    if order.pk not in pending:
        pending.append(order.pk)
        request.session[SCRATCH_PENDING_ORDERS_KEY] = pending
        request.session.modified = True


def _next_pending_order(request, campaign):
    order_ids = _pending_scratch_order_ids(request)
    if not order_ids:
        return None
    claimed_ids = set(OnlineGiftClaim.objects.filter(
        campaign=campaign, scratch_trigger_order_id__in=order_ids,
    ).values_list('scratch_trigger_order_id', flat=True))
    return next((order_id for order_id in order_ids if order_id not in claimed_ids), None)


def _remove_pending_order(request, order_id):
    request.session[SCRATCH_PENDING_ORDERS_KEY] = [
        value for value in _pending_scratch_order_ids(request) if value != order_id
    ]
    request.session.modified = True


def _set_scratch_session_reward(request, claim):
    """Obnovi serverski sačuvanu nagradu iz postojećeg zapisa, bez izvlačenja."""
    selected = _scratch_prize_for_claim(claim)
    if not selected:
        return False
    _, _, label, kind, percent, minimum, product_id = selected
    request.session[SCRATCH_SESSION_KEY] = claim.pk
    request.session[SESSION_REWARD_KEY] = {
        'claim_id': claim.pk,
        'type': claim.prize_type,
        'percent': str(percent),
        'free_shipping': kind == 'shipping',
        'label': label,
        'scratch_kind': kind,
        'minimum': str(minimum),
        'product_id': product_id,
        'expires_at': (claim.kreirano + timedelta(hours=24)).isoformat(),
    }
    return True


def scratch_label_for_claim(claim):
    selected = _scratch_prize_for_claim(claim)
    return selected[2] if selected else 'Sretni Greb-Greb nagrada'


def _scratch_claim_from_request(request):
    claim_id = request.session.get(SCRATCH_SESSION_KEY)
    if not claim_id:
        return None
    claim = OnlineGiftClaim.objects.filter(pk=claim_id, campaign__naziv=SCRATCH_CAMPAIGN_NAME).first()
    if not claim or claim.reward_consumed or claim.kreirano + timedelta(hours=24) <= timezone.now():
        return None
    return claim


def scratch_status(request):
    """Only reports eligibility; it never decides a reward."""
    if not request.session.session_key:
        request.session.create()
    campaign = _scratch_campaign()
    if not campaign.aktivan or not _available_scratch_prizes(campaign):
        return False
    # Ne prepisuj važeću osvojenu nagradu ako kupac napravi novu narudžbu
    # prije nego što je iskoristi. Sljedeća zarađena prilika ostaje u redu.
    current_reward = get_session_reward(request) or {}
    if (
        _scratch_claim_from_request(request)
        and current_reward.get('scratch_kind') not in (None, '', 'none')
    ):
        return False
    return bool(_next_pending_order(request, campaign))


def scratch_claim(request):
    """Atomically select and persist one reward for one completed order."""
    if not request.session.session_key:
        request.session.create()
    now = timezone.now()
    with transaction.atomic():
        campaign = _scratch_campaign()
        prizes = _available_scratch_prizes(campaign)
        if not campaign.aktivan or not prizes:
            return None, False
        trigger_order_id = _next_pending_order(request, campaign)
        if not trigger_order_id:
            return None, False
        existing = OnlineGiftClaim.objects.select_for_update().filter(
            campaign=campaign, scratch_trigger_order_id=trigger_order_id,
        ).first()
        if existing:
            _set_scratch_session_reward(request, existing)
            _remove_pending_order(request, trigger_order_id)
            return existing, False
        point = randbelow(sum(prize[1] for prize in prizes)) + 1
        total = 0
        for code, weight, label, kind, percent, minimum, product_id in prizes:
            total += weight
            if point <= total:
                selected = (code, label, kind, percent, minimum, product_id)
                break
        code, label, kind, percent, minimum, product_id = selected
        try:
            # Ugniježdeni savepoint čuva vanjsku transakciju upotrebljivom ako
            # unique ograničenje uhvati drugi istovremeni klik.
            with transaction.atomic():
                claim = OnlineGiftClaim.objects.create(
                    campaign=campaign, session_key=request.session.session_key,
                    user=request.user if request.user.is_authenticated else None,
                    won=kind != 'none', prize_type=OnlineGiftCampaign.PrizeType.PERCENT if kind == 'percent' else (
                        OnlineGiftCampaign.PrizeType.FREE_SHIPPING if kind == 'shipping' else ''),
                    discount_percent=percent,
                    scratch_prize_code=code,
                    scratch_trigger_order_id=trigger_order_id,
                )
        except IntegrityError:
            # Jedini mogući konkurentni slučaj: druga kartica je upravo upisala
            # isti sedmični pokušaj. Vraćamo taj rezultat, ne izvlačimo novi.
            claim = OnlineGiftClaim.objects.get(
                campaign=campaign, scratch_trigger_order_id=trigger_order_id,
            )
            _set_scratch_session_reward(request, claim)
            _remove_pending_order(request, trigger_order_id)
            return claim, False
        _set_scratch_session_reward(request, claim)
        _remove_pending_order(request, trigger_order_id)
        return claim, True


def scratch_cart_reward(request, subtotal):
    claim = _scratch_claim_from_request(request)
    reward = get_session_reward(request)
    if not claim or not reward:
        return {'claim': None, 'active': False, 'label': '', 'percent': Decimal('0'), 'free_shipping': False, 'remaining': Decimal('0'), 'gift_product': None}
    minimum = Decimal(str(reward.get('minimum') or 0))
    remaining = max(Decimal('0'), minimum - Decimal(str(subtotal or 0)))
    kind = reward.get('scratch_kind')
    active = remaining == 0 and kind != 'none'
    if kind == 'product_discount':
        # Prag je provjeren pri dodavanju artikla po redovnoj cijeni; sam
        # popust potom ne smije poništiti već validiranu nagradu.
        active = bool(reward.get('product_discount_added'))
    gift_product = None
    if active and kind == 'gift':
        candidate = None
        product_id = reward.get('product_id')
        if product_id:
            candidate = ScratchPrize.objects.filter(product_id=product_id).select_related('product').first()
            candidate = candidate.product if candidate else None
        if candidate is None:
            candidate = SiteSettings.load().sretni_greb_greb_poklon
        if candidate and candidate.aktivan and candidate.na_stanju:
            gift_product = candidate
    return {'claim': claim, 'active': active, 'label': reward.get('label') or '',
            'percent': Decimal(str(reward.get('percent') or 0)) if active and kind == 'percent' else Decimal('0'),
            'free_shipping': active and kind == 'shipping', 'remaining': remaining, 'kind': kind,
            'gift_product': gift_product}


def add_scratch_discount_product(request):
    """Dodaje jedini osvojeni artikal s popustom, nakon server-side provjere."""
    claim = _scratch_claim_from_request(request)
    reward = get_session_reward(request) or {}
    if not claim or reward.get('scratch_kind') != 'product_discount':
        return None, 'Ova Greb-Greb nagrada nije artikal s popustom.'
    try:
        product_id = int(reward.get('product_id'))
        percent = Decimal(str(reward.get('percent') or 0))
    except (TypeError, ValueError, InvalidOperation):
        return None, 'Nagrada nije ispravno podešena.'
    from .cart import Cart
    product = ScratchPrize.objects.filter(
        campaign=claim.campaign, product_id=product_id,
        kind=ScratchPrize.Kind.PRODUCT_DISCOUNT, active=True,
    ).select_related('product').first()
    product = product.product if product else None
    if not product or not product.aktivan or not product.na_stanju or percent <= 0:
        return None, 'Osvojeni artikal trenutno nije dostupan.'
    cart = Cart(request)
    minimum = Decimal(str(reward.get('minimum') or 0))
    regular = Decimal(str(product.bazna_cijena or 0))
    if cart.ukupno + regular < minimum:
        return None, f'Za ovu nagradu korpa mora imati najmanje {minimum:.2f} KM.'
    key = cart._line_key(product.pk, promo=True)
    if key in cart.cart:
        return product, ''
    discounted = (regular * (Decimal('1') - percent / Decimal('100'))).quantize(Decimal('0.01'))
    if not cart.add(product, quantity=1, custom_price=discounted, promo_bazna=regular,
                    discount_source='Sretni Greb-Greb — osvojeni artikal', discount_percent=percent):
        return None, 'Artikal nije moguće dodati u korpu.'
    reward['product_discount_added'] = True
    request.session[SESSION_REWARD_KEY] = reward
    request.session.modified = True
    return product, ''


def add_scratch_discount_product_to_order(request):
    """Dodaje osvojeni sniženi artikal samo na narudžbu koja je dala priliku."""
    claim = _scratch_claim_from_request(request)
    reward = get_session_reward(request) or {}
    if not claim or reward.get('scratch_kind') != 'product_discount' or not claim.scratch_trigger_order_id:
        return None, None, 'Ova nagrada nije dostupna za dodavanje na narudžbu.'
    try:
        product_id = int(reward.get('product_id'))
        percent = Decimal(str(reward.get('percent') or 0))
    except (TypeError, ValueError, InvalidOperation):
        return None, None, 'Nagrada nije ispravno podešena.'
    from .models import Order, OrderItem
    from .magacin import reserve_for_order
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=claim.scratch_trigger_order_id)
        if order.status != Order.Status.NOVA or order.zapakovana or order.stanje_skinuto:
            return None, None, 'Narudžba je već obrađena i artikal se više ne može dodati.'
        prize = ScratchPrize.objects.select_related('product').filter(
            campaign=claim.campaign, product_id=product_id,
            kind=ScratchPrize.Kind.PRODUCT_DISCOUNT, active=True,
        ).first()
        product = prize.product if prize else None
        if not product or not product.aktivan or not product.na_stanju or percent <= 0:
            return None, None, 'Osvojeni artikal trenutno nije dostupan.'
        if OrderItem.objects.filter(narudzba=order, artikal=product, popust_opis__startswith='Sretni Greb-Greb').exists():
            return product, order, ''
        # Ne slažemo akcijski i Greb-Greb popust: osvojeni popust polazi od
        # redovne cijene artikla.
        regular = Decimal(str(product.bazna_cijena))
        discounted = (regular * (Decimal('1') - percent / Decimal('100'))).quantize(Decimal('0.01'))
        if reserve_for_order(order, product, 1, napomena=f'Sretni Greb-Greb #{order.broj}'):
            return None, None, 'Osvojeni artikal više nije dostupan na lageru.'
        OrderItem.objects.create(
            narudzba=order, artikal=product, naziv=f'{product.naziv} — Sretni Greb-Greb',
            product_naziv=product.naziv, sifra=product.sifra or '', cijena=discounted,
            bazna_cijena=regular, popust_opis='Sretni Greb-Greb — osvojeni artikal',
            popust_postotak=percent, popust_iznos=(regular - discounted), kolicina=1,
        )
        details = list(order.popust_detalji or [])
        details.append({'opis': f'Sretni Greb-Greb: {product.naziv} −{percent}%', 'iznos': str(regular - discounted)})
        order.medjuzbir = (order.medjuzbir + discounted).quantize(Decimal('0.01'))
        order.ukupno = (order.ukupno + discounted).quantize(Decimal('0.01'))
        order.popust_detalji = details
        order.save(update_fields=['medjuzbir', 'ukupno', 'popust_detalji'])
        mark_reward_consumed(request, order=order)
    return product, order, ''



def get_session_reward(request):
    if not request:
        return None
    raw = request.session.get(SESSION_REWARD_KEY)
    if not isinstance(raw, dict):
        for k in _LEGACY_KEYS:
            raw = request.session.get(k)
            if isinstance(raw, dict) and not raw.get('consumed'):
                break
        else:
            return None
    if raw.get('consumed'):
        return None
    return raw


def clear_session_reward(request):
    if not request:
        return
    changed = False
    for key in (SESSION_REWARD_KEY,) + _LEGACY_KEYS:
        if key in request.session:
            del request.session[key]
            changed = True
    if changed:
        request.session.modified = True


def mark_reward_consumed(request, order=None):
    """Označi nagradu iskorištenom; veži na narudžbu ako je proslijeđena."""
    reward = get_session_reward(request)
    if not reward:
        return
    claim_id = reward.get('claim_id') or reward.get('spin_id')
    if claim_id:
        update_fields = {'reward_consumed': True}
        if order is not None and getattr(order, 'pk', None):
            update_fields['order_id'] = order.pk
        OnlineGiftClaim.objects.filter(pk=claim_id).update(**update_fields)
    clear_session_reward(request)


def reward_discount_percent(request):
    reward = get_session_reward(request)
    if reward and reward.get('scratch_kind'):
        return Decimal('0')
    if not reward or reward.get('type') != OnlineGiftCampaign.PrizeType.PERCENT:
        return Decimal('0')
    try:
        pct = Decimal(str(reward.get('percent') or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')
    return min(pct, Decimal('100')) if pct > 0 else Decimal('0')


def reward_discount_km(request):
    reward = get_session_reward(request)
    if not reward or reward.get('type') != OnlineGiftCampaign.PrizeType.FIXED_KM:
        return Decimal('0')
    try:
        km = Decimal(str(reward.get('km') or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')
    return km if km > 0 else Decimal('0')


def reward_free_shipping(request):
    reward = get_session_reward(request)
    if reward and reward.get('scratch_kind'):
        return False
    return bool(reward and reward.get('free_shipping'))


def active_reward_label(request):
    reward = get_session_reward(request)
    if not reward or reward.get('scratch_kind'):
        return ''
    return reward.get('label') or ''
