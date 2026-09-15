"""Iskorištavanje ranije dodijeljenih nagrada; kampanje i popup su uklonjeni."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


from .models import OnlineGiftCampaign, OnlineGiftClaim


SESSION_REWARD_KEY = 'online_gift_reward'
# legacy session keys from greb/wheel
_LEGACY_KEYS = (
    'greb_greb_reward',
    'prize_wheel_reward',
    'greb_greb_played',
    'prize_wheel_spun',
)



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
    return bool(reward and reward.get('free_shipping'))


def active_reward_label(request):
    reward = get_session_reward(request)
    if not reward:
        return ''
    return reward.get('label') or ''
