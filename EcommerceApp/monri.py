"""Monri hosted form: never collect card numbers, and trust signed callbacks only."""
import hashlib
import hmac
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import reverse


def configured():
    try:
        url = urlsplit(settings.MONRI_PUBLIC_BASE_URL)
    except ValueError:
        return False
    return bool(settings.MONRI_ENABLED and settings.MONRI_MERCHANT_KEY and settings.MONRI_AUTHENTICITY_TOKEN
                and settings.MONRI_ENVIRONMENT in ('test', 'production')
                and url.scheme == 'https' and url.hostname and not url.username and not url.password
                and not url.query and not url.fragment and not url.path.strip('/'))


def require_paid_for_fulfillment(order):
    payment = getattr(order, 'card_payment', None)
    if payment and order.status == order.Status.OTKAZANA:
        raise ValueError('Otkazana narudžba nije dostupna za isporuku.')
    if payment and payment.status != 'paid':
        raise ValueError('Narudžba čeka potvrdu kartičnog plaćanja. Isporuka nije dozvoljena.')
    if payment and int(order.ukupno * 100) != payment.amount:
        raise ValueError('Iznos narudžbe razlikuje se od kartične uplate. Potrebna je provjera prije isporuke.')


def form_data(payment):
    if not configured() or payment.environment != settings.MONRI_ENVIRONMENT:
        raise ValueError('Kartično plaćanje trenutno nije dostupno.')
    order = payment.order
    amount = str(payment.amount)
    if payment.amount <= 0 or order.status == order.Status.OTKAZANA:
        raise ValueError('Ova narudžba nije dostupna za kartično plaćanje.')
    base = settings.MONRI_PUBLIC_BASE_URL
    fields = dict(authenticity_token=settings.MONRI_AUTHENTICITY_TOKEN,
        order_number=order.broj, amount=amount, currency=payment.currency,
        ch_full_name=order.ime_prezime, ch_email=order.email,
        ch_address=order.adresa, ch_city=order.grad, ch_zip=order.postanski_broj,
        ch_country='BA', ch_phone=order.telefon,
        order_info='Carpologija narudžba ' + order.broj, transaction_type='purchase', language='hr',
        success_url_override=base + reverse('monri_return', args=[payment.token]),
        cancel_url=base + reverse('monri_cancel', args=[payment.token]),
        callback_url=base + reverse('monri_callback'))
    fields['digest'] = hashlib.sha512((settings.MONRI_MERCHANT_KEY + order.broj + amount + payment.currency).encode()).hexdigest()
    endpoint = 'https://ipgtest.monri.com/v2/form' if payment.environment == 'test' else 'https://ipg.monri.com/v2/form'
    return endpoint, fields


def callback_valid(request):
    if not configured() or len(request.body) > 65536:
        return False
    expected = 'WP3-callback ' + hashlib.sha512(settings.MONRI_MERCHANT_KEY.encode() + request.body).hexdigest()
    return hmac.compare_digest(request.headers.get('Authorization', ''), expected)
