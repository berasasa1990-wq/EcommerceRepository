"""Monri hosted form: never collect card numbers, and trust signed callbacks only."""
import hashlib
import hmac
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import reverse


FORM_ENDPOINTS = {
    'test': 'https://ipgtest.monri.com/v2/form',
    'production': 'https://ipg.monri.com/v2/form',
}


def configured():
    try:
        public_url = settings.MONRI_PUBLIC_BASE_URL
        if any(char.isspace() for char in public_url) or '\\' in public_url:
            return False
        url = urlsplit(public_url)
        if url.port == 0:
            return False
    except ValueError:
        return False
    return bool(settings.MONRI_ENABLED and settings.MONRI_MERCHANT_KEY and settings.MONRI_AUTHENTICITY_TOKEN
                and settings.MONRI_ENVIRONMENT in FORM_ENDPOINTS
                and (settings.MONRI_ENVIRONMENT != 'production'
                     or public_url.rstrip('/') == 'https://carpologijabh.ba')
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
        cancel_url_override=base + reverse('monri_cancel', args=[payment.token]),
        callback_url_override=base + reverse('monri_callback'))
    # Sign the exact strings passed to the HTML form, without separators or token.
    fields['digest'] = hashlib.sha512((settings.MONRI_MERCHANT_KEY
        + fields['order_number'] + fields['amount'] + fields['currency']).encode('utf-8')).hexdigest()
    endpoint = FORM_ENDPOINTS[settings.MONRI_ENVIRONMENT]
    # Deliberate allowlist: never log the payload, credentials, digest or buyer data.
    logging.getLogger(__name__).info(
        'MONRI_FORM_REQUEST environment=%s endpoint=%s order_number=%s amount=%s currency=%s '
        'field_names=%s merchant_key_length=%s authenticity_token_length=%s '
        'digest_algorithm=SHA-512 digest_encoding=UTF-8',
        settings.MONRI_ENVIRONMENT, endpoint, fields['order_number'], fields['amount'], fields['currency'],
        sorted(fields), len(settings.MONRI_MERCHANT_KEY), len(settings.MONRI_AUTHENTICITY_TOKEN))
    return endpoint, fields


def callback_valid(request):
    if not configured() or len(request.body) > 65536:
        return False
    expected = 'WP3-callback ' + hashlib.sha512(settings.MONRI_MERCHANT_KEY.encode() + request.body).hexdigest()
    return hmac.compare_digest(request.headers.get('Authorization', ''), expected)
