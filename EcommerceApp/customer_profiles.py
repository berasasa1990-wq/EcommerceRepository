"""Customer overview and password reset from the staff profile editor."""
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods

from .models import ActiveCartItem, Coupon, LoyaltyCard, Order

logger = logging.getLogger(__name__)


def profile_overview(profile, namespace):
    user = profile.user
    orders = Order.objects.filter(korisnik=user).order_by('-pk')
    card = LoyaltyCard.objects.filter(user=user).first()
    coupons = Coupon.objects.filter(Q(vlasnik=user) | Q(loyalty_kartica__user=user)).distinct()
    recent_orders = [{'order': order, 'url': reverse(f'{namespace}:EcommerceApp_order_change', args=[order.pk])}
                     for order in orders[:30]]
    context = {
        'customer': user, 'profile': profile, 'orders': recent_orders,
        'order_count': orders.count(),
        'order_total': orders.exclude(status=Order.Status.OTKAZANA).aggregate(total=Sum('ukupno'))['total'] or 0,
        'orders_url': reverse(f'{namespace}:EcommerceApp_order_changelist') + f'?korisnik__id__exact={user.pk}',
        'card': card, 'coupons': coupons,
        'card_url': reverse(f'{namespace}:EcommerceApp_loyaltycard_change', args=[card.pk]) if card else None,
        'purchases': card.evidentirane_kupovine.order_by('-pk')[:30] if card else [],
        'loyalty_url': reverse('staff_loyalty_system') + '?' + urlencode({'q': user.email or user.username}),
        'cart_items': ActiveCartItem.objects.filter(user=user).order_by('-azurirano')[:30],
        'cart_url': reverse(f'{namespace}:EcommerceApp_activecartitem_changelist') + f'?user__id__exact={user.pk}',
    }
    # The template escapes all customer data; only its rendered markup is trusted.
    return mark_safe(render_to_string('admin/customer_profile_overview.html', context))


@require_http_methods(['GET', 'POST'])
def reset_profile_password(request, editor, profile_id):
    profile = get_object_or_404(editor.get_queryset(request), pk=profile_id, user__is_superuser=False)
    if not editor.has_change_permission(request, profile):
        raise PermissionDenied
    user = profile.user
    back_url = reverse(f'{editor.admin_site.name}:EcommerceApp_userprofile_change', args=[profile.pk])
    eligible = bool(user.email and user.is_active and user.has_usable_password())
    if request.method == 'POST':
        if not eligible:
            editor.message_user(request, 'Reset nije moguć za ovog korisnika.', messages.ERROR)
            return redirect(back_url)
        # Use the current shop host, including its local port, for this database.
        context = {'protocol': 'https' if request.is_secure() else 'http', 'domain': request.get_host(),
                   'uid': urlsafe_base64_encode(force_bytes(user.pk)),
                   'token': default_token_generator.make_token(user)}
        body = render_to_string('auth/admin_password_reset_email.txt', context)
        subject = render_to_string('auth/admin_password_reset_subject.txt').strip()
        try:
            sent = send_mail(subject, body, None, [user.email], fail_silently=False)
        except Exception:
            logger.exception('Slanje reset lozinke nije uspjelo za profil %s', profile.pk)
            sent = 0
        if sent and settings.EMAIL_BACKEND.endswith(('console.EmailBackend', 'filebased.EmailBackend', 'locmem.EmailBackend')):
            text = 'Reset email je generisan u razvojnom email backendu; nije dostavljen u stvarno sanduče.'
            level = messages.WARNING
        else:
            text = 'Link za reset lozinke je poslan na email.' if sent else 'Email nije poslan. Provjerite email konfiguraciju i pokušajte ponovo.'
            level = messages.SUCCESS if sent else messages.ERROR
        editor.message_user(request, text, level)
        return redirect(back_url)
    return render(request, 'admin/profile_reset_password.html', {
        **editor.admin_site.each_context(request), 'title': 'Reset lozinke',
        'customer': user, 'eligible': eligible, 'back_url': back_url, 'opts': editor.model._meta,
    })
