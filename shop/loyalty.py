from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Sum, F, Q
from .models import LoyaltyTier


def loyalty_status(user):
    from .modules import loyalty_enabled
    if not user.is_authenticated or not loyalty_enabled():
        return Decimal('0'), Decimal('0')
    spent = user.shop_orders.filter(status__in=['placed', 'shipped', 'completed']).aggregate(value=Sum(F('total') - F('delivery_price')))['value'] or Decimal('0')
    if not rules().tiers_enabled:
        return spent, Decimal('0')
    tier = LoyaltyTier.objects.filter(minimum__lte=spent).filter(Q(maximum__isnull=True) | Q(maximum__gt=spent)).order_by('-minimum').first()
    return spent, tier.percent if tier else Decimal('0')


def loyalty_amount(subtotal, percent):
    return (subtotal * percent / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def applied_loyalty_percent(request):
    if request.user.is_authenticated and request.session.get('loyalty_customer') == request.user.pk:
        return loyalty_status(request.user)[1]
    return Decimal('0')


from django.db import transaction
from django.db.models import F
from .models import LoyaltyCard, LoyaltyEntry, LoyaltyRules, Order
from bera_licensing.services import require_module


def rules():
    config = LoyaltyRules.objects.filter(pk=1).first() or LoyaltyRules()
    config.point_value = Decimal(config.point_value)
    return config


def point_quote(user, subtotal, requested):
    from .modules import loyalty_enabled
    if not user.is_authenticated or not loyalty_enabled():
        return 0, Decimal('0.00')
    try:
        requested = max(0, int(requested))
    except (ValueError, TypeError):
        requested = 0
    card = LoyaltyCard.objects.filter(customer=user).first()
    config = rules()
    if not config.points_enabled:
        return 0, Decimal('0.00')
    limit = int(max(Decimal(0), subtotal) * config.max_discount_percent / 100 / config.point_unit_value)
    points = min(requested, max(0, card.points) if card else 0, limit)
    return points, (points * config.point_unit_value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


@transaction.atomic
def redeem_points(order, user, subtotal, requested):
    require_module('loyalty')
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != 'placed':
        raise ValueError('Bodovi se koriste samo pri naručivanju.')
    if order.customer_id != user.pk or not user.is_authenticated:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    card, _ = LoyaltyCard.objects.get_or_create(customer=user)
    card = LoyaltyCard.objects.select_for_update().get(pk=card.pk)
    existing = LoyaltyEntry.objects.filter(order=order, kind='redeem').first()
    if existing:
        raise ValueError('Bodovi su već iskorišteni za ovu narudžbu.')
    points, discount = point_quote(user, subtotal, requested)
    if points:
        changed = LoyaltyCard.objects.filter(pk=card.pk, points__gte=points).update(points=F('points')-points)
        if not changed:
            raise ValueError('Stanje bodova je promijenjeno.')
        LoyaltyEntry.objects.create(card=card, order=order, kind='redeem', points=-points)
    return discount


@transaction.atomic
def settle_points(order_id):
    require_module('loyalty')
    order = Order.objects.select_for_update().get(pk=order_id)
    if not order.customer_id or order.status not in ('shipped', 'completed', 'cancelled'):
        return
    card, _ = LoyaltyCard.objects.get_or_create(customer_id=order.customer_id)
    card = LoyaltyCard.objects.select_for_update().get(pk=card.pk)
    if order.status in ('shipped', 'completed'):
        if not rules().points_enabled:
            return
        if order.point_entries.filter(kind__in=['earn', 'reverse', 'refund']).exists():
            return
        config = rules()
        points = int(max(Decimal(0), order.total-order.delivery_price) * config.points_per_km / config.award_spend)
        if points:
            LoyaltyEntry.objects.create(card=card, order=order, kind='earn', points=points)
            LoyaltyCard.objects.filter(pk=card.pk).update(points=F('points')+points)
    else:
        earned = order.point_entries.filter(kind='earn').first()
        # An already spent award can create a debt, recovered before future redemption.
        if earned and not order.point_entries.filter(kind='reverse').exists():
            LoyaltyEntry.objects.create(card=card, order=order, kind='reverse', points=-earned.points)
            LoyaltyCard.objects.filter(pk=card.pk).update(points=F('points')-earned.points)
        spent = order.point_entries.filter(kind='redeem').first()
        if spent and not order.point_entries.filter(kind='refund').exists():
            LoyaltyEntry.objects.create(card=card, order=order, kind='refund', points=-spent.points)
            LoyaltyCard.objects.filter(pk=card.pk).update(points=F('points')-spent.points)
