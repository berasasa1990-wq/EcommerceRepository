from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User

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


def send_cart_recovery_alert(session_key, *, discount_percent=0, staff_user=None):
    if not session_key:
        raise ValueError('Sesija korpe nije pronađena.')
    percent = _clamp_percent(discount_percent)
    alert, _created = CartRecoveryAlert.objects.update_or_create(
        session_key=session_key,
        defaults={
            'discount_percent': percent,
            'show_popup': True,
            'discount_applied': False,
            'poslao': staff_user if isinstance(staff_user, User) else None,
        },
    )
    return alert


def get_active_cart_recovery_alert(request, cart):
    if cart is None or not cart.item_count:
        return None
    if cart.get_recovery_discount_percent():
        return None
    session_key = get_cart_session_key(request)
    if not session_key:
        return None
    return CartRecoveryAlert.objects.filter(
        session_key=session_key,
        show_popup=True,
        discount_applied=False,
    ).first()


def apply_cart_recovery_discount(request, cart):
    session_key = get_cart_session_key(request)
    if not session_key or not cart.item_count:
        return False, 'Korpa je prazna.'

    alert = CartRecoveryAlert.objects.filter(
        session_key=session_key,
        show_popup=True,
        discount_applied=False,
    ).first()
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
    session_key = get_cart_session_key(request)
    if not session_key:
        return
    CartRecoveryAlert.objects.filter(
        session_key=session_key,
        show_popup=True,
    ).update(show_popup=False)