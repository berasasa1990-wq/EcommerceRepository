import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.db import transaction
from django.db.models import Q
from django.core import serializers
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.cache import never_cache

from .models import CardPayment, Order
from .monri import callback_valid, form_data, checkout_order


logger = logging.getLogger('EcommerceApp.monri')


def finish_paid_order(order):
    """Durable step markers let signed callback retries resume unfinished work."""
    from .emails import queue_order_emails
    from .views import azuriraj_loyalty_nakon_narudzbe, sync_korisnik, sync_narudzba, _save_profile_from_checkout
    from .staff_alerts import notify_purchase
    from .views_magacin import invalidate_magacin_nav_counts
    from .meta_conversions import track_purchase
    from .views_magacin import _save_warehouse_customer
    from types import SimpleNamespace
    from django.contrib.auth.models import AnonymousUser
    payment = CardPayment.objects.get(order=order)
    context = payment.checkout_context or {}
    browser = SimpleNamespace(META=context.get('meta', {}), COOKIES=context.get('cookies', {}),
                              user=order.korisnik or AnonymousUser(),
                              build_absolute_uri=lambda: settings.MONRI_PUBLIC_BASE_URL + reverse('order_success', args=[order.broj]))

    def loyalty():
        card = azuriraj_loyalty_nakon_narudzbe(order)
        if card:
            sync_korisnik(card.user)
        elif order.korisnik_id:
            sync_korisnik(order.korisnik)

    def order_sync():
        result = sync_narudzba(order)
        if isinstance(result, dict) and not result.get('ok', True):
            raise RuntimeError('Order sync failed')

    def rewards():
        from .online_gift import SESSION_REWARD_KEY, mark_reward_consumed
        from .live_visitor_offer import consume_registration_reward
        if context.get('consume_reward') and context.get('reward'):
            # Consume the reward captured at checkout, not a newer browser reward.
            class RewardSession(dict):
                modified = False
            request = SimpleNamespace(session=RewardSession({SESSION_REWARD_KEY: context['reward']}))
            mark_reward_consumed(request, order=order)
        if order.korisnik_id:
            consume_registration_reward(order.korisnik)

    def session_updates():
        from importlib import import_module
        from .online_gift import grant_scratch_chance, get_session_reward, clear_session_reward
        from .live_visitor_offer import clear_free_shipping_reward
        from .cart import Cart
        session_key = context.get('session_key')
        if not session_key:
            return
        session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)
        request = SimpleNamespace(session=session, user=browser.user, META=browser.META)
        if context.get('consume_reward') and get_session_reward(request) == context.get('reward'):
            clear_session_reward(request)
        if dict(session.get('cart', {})) == context.get('cart'):
            Cart(request).clear()
            clear_free_shipping_reward(request, browser.user)
        grant_scratch_chance(request, order)
        session.save()

    steps = (
        ('customer', lambda: _save_warehouse_customer(ime=order.ime_prezime, telefon=order.telefon,
            adresa=order.adresa, grad=order.grad, email=order.email, postanski_broj=order.postanski_broj,
            update_existing=False)),
        ('profile', lambda: _save_profile_from_checkout(order.korisnik, {
            'ime_prezime': order.ime_prezime, 'email': order.email, 'telefon': order.telefon,
            'adresa': order.adresa, 'grad': order.grad, 'postanski_broj': order.postanski_broj,
        }) if order.korisnik_id else None),
        ('rewards', rewards),
        ('session', session_updates),
        ('staff', lambda: notify_purchase(ime=order.ime_prezime, email=order.email, grad=order.grad,
            session_key=context.get('session_key', ''), order_number=order.broj,
            total=str(order.ukupno), shipping=order.dostava_naziv)),
        ('email', lambda: queue_order_emails(order)),
        ('loyalty', loyalty),
        ('order_sync', order_sync),
        ('analytics', lambda: track_purchase(browser, order, event_id=f'purchase-{order.broj}')),
        ('navigation', invalidate_magacin_nav_counts),
    )
    for name, action in steps:
        try:
            with transaction.atomic():
                locked = CardPayment.objects.select_for_update().get(pk=payment.pk)
                if locked.status != 'paid' or not locked.order_id:
                    return False
                if locked.finalized_at:
                    return True
                if name in locked.finalization_steps:
                    continue
                action()
                locked.finalization_steps = {**locked.finalization_steps, name: True}
                locked.save(update_fields=['finalization_steps'])
        except Exception:
            # Do not print exception contents: third-party errors may contain secrets.
            logger.warning('MONRI_ORDER_FINALIZATION_PENDING payment_id=%s order_id=%s step=%s', payment.pk, order.pk, name)
            return False
    with transaction.atomic():
        locked = CardPayment.objects.select_for_update().get(pk=payment.pk)
        if locked.finalized_at:
            return True
        locked.finalized_at = timezone.now()
        locked.save(update_fields=['finalized_at'])
    logger.info('MONRI_ORDER_FINALIZED payment_id=%s order_id=%s status=paid', payment.pk, order.pk)
    return True


def payment_render(request, template, context, status=200):
    response = render(request, template, context, status=status)
    response['Referrer-Policy'] = 'no-referrer'
    return response


def payment_context(payment):
    return {'payment': payment, 'order': checkout_order(payment)}


def paid_redirect(payment):
    response = redirect('order_success', broj=payment.order.broj)
    response['Referrer-Policy'] = 'no-referrer'
    return response


@never_cache
@require_GET
def start(request, token):
    payment = get_object_or_404(CardPayment.objects.select_related('order'), token=token)
    if payment.status == 'paid':
        return paid_redirect(payment)
    try:
        endpoint, fields = form_data(payment)
    except ValueError as error:
        return payment_render(request, 'monri_status.html', {**payment_context(payment), 'payment_error': str(error)}, status=503)
    return payment_render(request, 'monri_form.html', {**payment_context(payment), 'endpoint': endpoint, 'fields': fields})


@never_cache
@require_GET
def payment_return(request, token):
    # A browser redirect is not proof of payment. Only the signed callback pays.
    payment = get_object_or_404(CardPayment.objects.select_related('order'), token=token)
    if payment.status == 'paid':
        return paid_redirect(payment)
    return payment_render(request, 'monri_status.html', {**payment_context(payment), 'waiting_return': True})


@never_cache
@require_GET
def payment_status(request, token):
    # Read-only: browser input cannot update payment or trigger finalization.
    payment = get_object_or_404(CardPayment.objects.select_related('order'), token=token)
    response = JsonResponse({'status': payment.status,
        'success_url': reverse('order_success', args=[payment.order.broj])
                       if payment.status == 'paid' and payment.order_id else None})
    response['Referrer-Policy'] = 'no-referrer'
    return response


@never_cache
@require_GET
def cancel(request, token):
    payment = get_object_or_404(CardPayment.objects.select_related('order'), token=token)
    # Do not cancel/release stock based on an untrusted browser redirect.
    return payment_render(request, 'monri_status.html', {**payment_context(payment), 'cancelled': True})


@csrf_exempt
@require_POST
def callback(request):
    logger.info('MONRI_CALLBACK_RECEIVED environment=%s', settings.MONRI_ENVIRONMENT)
    if not callback_valid(request):
        logger.warning('MONRI_CALLBACK_REJECTED reason=signature_or_configuration')
        return HttpResponse(status=403)
    try:
        body = json.loads(request.body)
        if (not isinstance(body, dict) or type(body.get('amount')) is not int
                or not isinstance(body.get('order_number'), str) or not body['order_number']):
            return HttpResponse(status=400)
    except (ValueError, UnicodeDecodeError):
        logger.warning('MONRI_CALLBACK_REJECTED reason=invalid_json')
        return HttpResponse(status=400)
    result = {'ok': True}
    with transaction.atomic():
        payment = callback_payment_queryset(body['order_number']).first()
        # The locking query targets CardPayment only, not its nullable Order join.
        if not payment:
            logger.warning('MONRI_CALLBACK_REJECTED reason=payment_not_found')
            return HttpResponse(status=404)
        if (body.get('amount') != payment.amount or body.get('currency') != payment.currency
                or payment.environment != settings.MONRI_ENVIRONMENT
                or body.get('transaction_type') != 'purchase'):
            logger.warning('MONRI_CALLBACK_REJECTED payment_id=%s reason=amount_currency_environment_or_type', payment.pk)
            return HttpResponse(status=400)
        if body.get('status') != 'approved' or body.get('response_code') != '0000':
            logger.warning('MONRI_CALLBACK_REJECTED payment_id=%s reason=status_or_response_code', payment.pk)
            return HttpResponse(status=400)
        transaction_id = str(body.get('id') or '')
        if not transaction_id or len(transaction_id) > 64:
            return HttpResponse(status=400)
        logger.info('MONRI_CALLBACK_VERIFIED payment_id=%s environment=%s', payment.pk, payment.environment)
        if payment.status == 'paid':
            if payment.transaction_id != transaction_id:
                return HttpResponse(status=409)
            if not payment.finalized_at:
                transaction.on_commit(lambda: result.update(ok=finish_paid_order(payment.order) is not False))
        else:
            if payment.order_id and Order.objects.select_for_update().get(pk=payment.order_id).status == Order.Status.OTKAZANA:
                return HttpResponse(status=409)
            complete_verified_payment(payment, transaction_id)
            transaction.on_commit(lambda: result.update(ok=finish_paid_order(payment.order) is not False))
    if not result['ok']:
        return HttpResponse(status=503)  # Monri retries; completed steps are not repeated.
    return JsonResponse({'ok': True})


def callback_payment_queryset(order_number):
    return CardPayment.objects.select_for_update(of=('self',)).filter(
        Q(reference=order_number) | Q(order__broj=order_number))


def complete_verified_payment(payment, transaction_id):
    if payment.order_id:
        order = Order.objects.select_for_update().get(pk=payment.order_id)
    else:
        snapshot = list(serializers.deserialize('json', json.dumps(payment.checkout_snapshot)))
        order = snapshot[0].object
        order.pk = None
        order.broj = ''  # Assign a real order number only after verified payment.
        order.status = Order.Status.NOVA
        order.save()
        for entry in snapshot[1:]:
            item = entry.object
            item.pk = None
            item.narudzba = order
            item.save()
        payment.order = order
        from .magacin import reserve_web_order_stock, MagacinError
        try:
            with transaction.atomic():
                reserve_web_order_stock(order)
        except MagacinError:
            # Preserve the verified paid order for staff handling, even if stock changed.
            logging.getLogger(__name__).warning('Plaćena kartična narudžba ID %s zahtijeva provjeru lagera.', order.pk)
    if order.status == Order.Status.CEKA_PLACANJE:
        order.status = Order.Status.NOVA
        order.kreirana = timezone.now()
        order.save(update_fields=['status', 'kreirana'])
    payment.status = 'paid'
    payment.transaction_id = transaction_id
    payment.paid_at = timezone.now()
    payment.save(update_fields=['order', 'status', 'transaction_id', 'paid_at'])
    logger.info('MONRI_PAYMENT_MARKED_PAID payment_id=%s order_id=%s status=paid', payment.pk, order.pk)
