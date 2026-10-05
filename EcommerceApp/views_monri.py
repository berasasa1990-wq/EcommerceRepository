import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.cache import never_cache

from .models import CardPayment
from .monri import callback_valid, form_data


def finish_paid_order(order):
    from .emails import queue_order_emails
    from .views import azuriraj_loyalty_nakon_narudzbe, sync_korisnik, sync_narudzba
    queue_order_emails(order)
    try:
        card = azuriraj_loyalty_nakon_narudzbe(order)
        if card:
            sync_korisnik(card.user)
    except Exception:
        logging.getLogger(__name__).exception('Loyalty sync kartične narudžbe ID %s nije uspio.', order.pk)
    try:
        sync_narudzba(order)
    except Exception:
        logging.getLogger(__name__).exception('Sync kartične narudžbe ID %s nije uspio.', order.pk)


def payment_render(request, template, context, status=200):
    response = render(request, template, context, status=status)
    response['Referrer-Policy'] = 'no-referrer'
    return response


def payment_context(payment):
    return {'payment': payment, 'order': payment.order}


@never_cache
@require_GET
def start(request, token):
    payment = get_object_or_404(CardPayment.objects.select_related('order'), token=token)
    if payment.status == 'paid':
        return payment_render(request, 'monri_status.html', payment_context(payment))
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
    return payment_render(request, 'monri_status.html', payment_context(payment))


@never_cache
@require_GET
def cancel(request, token):
    payment = get_object_or_404(CardPayment.objects.select_related('order'), token=token)
    # Do not cancel/release stock based on an untrusted browser redirect.
    return payment_render(request, 'monri_status.html', {**payment_context(payment), 'cancelled': True})


@csrf_exempt
@require_POST
def callback(request):
    if not callback_valid(request):
        return HttpResponse(status=403)
    try:
        body = json.loads(request.body)
        if not isinstance(body, dict) or type(body.get('amount')) is not int:
            return HttpResponse(status=400)
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)
    with transaction.atomic():
        payment = CardPayment.objects.select_for_update().filter(order__broj=body.get('order_number')).first()
        if not payment:
            return HttpResponse(status=404)
        if (body.get('amount') != payment.amount or body.get('currency') != payment.currency
                or payment.environment != settings.MONRI_ENVIRONMENT
                or body.get('transaction_type') != 'purchase'):
            return HttpResponse(status=400)
        if body.get('status') != 'approved' or body.get('response_code') != '0000':
            return HttpResponse(status=400)
        transaction_id = str(body.get('id') or '')
        if not transaction_id or len(transaction_id) > 64:
            return HttpResponse(status=400)
        if payment.status == 'paid':
            return HttpResponse(status=200 if payment.transaction_id == transaction_id else 409)
        payment.status = 'paid'
        payment.transaction_id = transaction_id
        payment.paid_at = timezone.now()
        payment.save(update_fields=['status', 'transaction_id', 'paid_at'])
        transaction.on_commit(lambda: finish_paid_order(payment.order))
    return JsonResponse({'ok': True})
