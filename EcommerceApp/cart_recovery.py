from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.db.models import Q

from .cart_tracking import get_cart_session_key
from .models import CartRecoveryAlert


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


def _recovery_lookup_q(request):
    session_key = get_cart_session_key(request)
    clauses = Q()
    if session_key:
        clauses |= Q(session_key=session_key)
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        clauses |= Q(user=user)
    return clauses


def send_cart_recovery_alert(session_key, *, discount_percent=0, staff_user=None, target_user=None):
    if not session_key:
        raise ValueError('Sesija korpe nije pronađena.')
    percent = _clamp_percent(discount_percent)
    defaults = {
        'discount_percent': percent,
        'show_popup': True,
        'discount_applied': False,
        'poslao': staff_user if isinstance(staff_user, User) else None,
        'session_key': session_key,
    }

    if target_user and not isinstance(target_user, User):
        target_user = None

    if target_user:
        defaults['user'] = target_user
        alert = CartRecoveryAlert.objects.filter(user=target_user).first()
        if not alert:
            alert = CartRecoveryAlert.objects.filter(session_key=session_key).first()
        if alert:
            for field, value in defaults.items():
                setattr(alert, field, value)
            alert.save()
        else:
            CartRecoveryAlert.objects.filter(session_key=session_key).delete()
            alert = CartRecoveryAlert.objects.create(**defaults)
    else:
        defaults['user'] = None
        alert = CartRecoveryAlert.objects.filter(
            session_key=session_key,
            user__isnull=True,
        ).first()
        if alert:
            for field, value in defaults.items():
                setattr(alert, field, value)
            alert.save()
        else:
            CartRecoveryAlert.objects.filter(session_key=session_key, user__isnull=True).delete()
            alert = CartRecoveryAlert.objects.create(**defaults)
    return alert


def get_active_cart_recovery_alert(request, cart):
    if cart is None or not cart.item_count:
        return None
    if cart.get_recovery_discount_percent():
        return None
    lookup = _recovery_lookup_q(request)
    if not lookup:
        return None
    return CartRecoveryAlert.objects.filter(
        lookup,
        show_popup=True,
        discount_applied=False,
    ).order_by('-azurirano').first()


def apply_cart_recovery_discount(request, cart):
    if not cart.item_count:
        return False, 'Korpa je prazna.'

    lookup = _recovery_lookup_q(request)
    if not lookup:
        return False, 'Nema aktivnog popusta za ovu korpu.'

    alert = CartRecoveryAlert.objects.filter(
        lookup,
        show_popup=True,
        discount_applied=False,
    ).order_by('-azurirano').first()
    if not alert:
        return False, 'Nema aktivnog popusta za ovu korpu.'

    percent = alert.discount_percent or Decimal('0')
    if percent > 0:
        cart.set_recovery_discount(percent)
    alert.discount_applied = True
    alert.show_popup = False
    alert.save(update_fields=['discount_applied', 'show_popup', 'azurirano'])
    return True, percent


def dismiss_cart_recovery_alert(request):
    lookup = _recovery_lookup_q(request)
    if not lookup:
        return
    CartRecoveryAlert.objects.filter(
        lookup,
        show_popup=True,
    ).update(show_popup=False)