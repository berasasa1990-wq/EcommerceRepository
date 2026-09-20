"""Meta Conversions API (server-side events) for Facebook / Instagram ads."""

import hashlib
import logging
import threading
import time
import uuid
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

META_API_VERSION = 'v21.0'
CURRENCY = 'BAM'


def is_configured():
    return bool(getattr(settings, 'META_PIXEL_ID', '') and getattr(settings, 'META_ACCESS_TOKEN', ''))


def _sha256_hash(value):
    if not value:
        return None
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _normalize_email(email):
    return (email or '').strip().lower()


def _normalize_phone(phone):
    digits = ''.join(character for character in (phone or '') if character.isdigit())
    if digits.startswith('00'):
        digits = digits[2:]
    if digits.startswith('0') and len(digits) >= 9:
        digits = '387' + digits[1:]
    elif not digits.startswith('387') and len(digits) == 8:
        digits = '387' + digits
    return digits


def hash_email(email):
    normalized = _normalize_email(email)
    return _sha256_hash(normalized) if normalized else None


def hash_phone(phone):
    normalized = _normalize_phone(phone)
    return _sha256_hash(normalized) if normalized else None


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _base_user_data(request):
    if not request:
        return {}
    user_data = {
        'client_ip_address': _client_ip(request),
        'client_user_agent': (request.META.get('HTTP_USER_AGENT') or '')[:500],
    }
    fbp = request.COOKIES.get('_fbp')
    fbc = request.COOKIES.get('_fbc')
    if fbp:
        user_data['fbp'] = fbp
    if fbc:
        user_data['fbc'] = fbc
    return user_data


def build_user_data(request, *, email=None, phone=None, first_name=None, last_name=None):
    user_data = _base_user_data(request)
    em_hash = hash_email(email)
    ph_hash = hash_phone(phone)
    if em_hash:
        user_data['em'] = [em_hash]
    if ph_hash:
        user_data['ph'] = [ph_hash]
    if first_name:
        fn_hash = _sha256_hash(first_name.strip().lower())
        if fn_hash:
            user_data['fn'] = [fn_hash]
    if last_name:
        ln_hash = _sha256_hash(last_name.strip().lower())
        if ln_hash:
            user_data['ln'] = [ln_hash]
    return user_data


def _event_email_user_data(request):
    if not request:
        return {}
    user = getattr(request, 'user', None)
    email = getattr(user, 'email', '') if getattr(user, 'is_authenticated', False) else ''
    if not email:
        session = getattr(request, 'session', None)
        email = session.get('checkout_email', '') if session is not None else ''
    em_hash = hash_email(email) if isinstance(email, str) else None
    return {'em': [em_hash]} if em_hash else {}


def build_content_item(content_id, quantity=1, price=None):
    item = {
        'id': str(content_id),
        'quantity': int(quantity),
    }
    if price is not None:
        item['item_price'] = float(price)
    return item


def send_event(
    request,
    event_name,
    *,
    event_id=None,
    user_data=None,
    custom_data=None,
    event_source_url=None,
):
    if not is_configured():
        _log_meta_result('skipped_configuration', event_name, event_id)
        return event_id

    if not event_id:
        event_id = f'{event_name.lower()}-{uuid.uuid4().hex}'

    payload_user_data = _base_user_data(request)
    if user_data:
        payload_user_data.update(user_data)

    event = {
        'event_name': event_name,
        'event_time': int(time.time()),
        'event_id': event_id,
        'action_source': 'website',
        'user_data': payload_user_data,
    }
    if event_source_url:
        event['event_source_url'] = event_source_url
    elif request:
        event['event_source_url'] = request.build_absolute_uri()
    if custom_data:
        event['custom_data'] = custom_data

    url = f'https://graph.facebook.com/{META_API_VERSION}/{settings.META_PIXEL_ID}/events'
    body = {
        'data': [event],
        'access_token': settings.META_ACCESS_TOKEN,
    }
    test_event_code = (getattr(settings, 'META_TEST_EVENT_CODE', '') or '').strip()
    if test_event_code:
        body['test_event_code'] = test_event_code
    # Ne blokiraj request (timeout 10s na 1 worker = cijeli sajt stoji).
    try:
        threading.Thread(
            target=_post_meta_event,
            args=(url, body, event_name, event_id),
            daemon=True,
        ).start()
    except Exception as exc:
        _log_meta_result(
            'thread_start_error', event_name, event_id,
            test_event_code_present=bool(body.get('test_event_code')),
            em_present=bool(body['data'][0]['user_data'].get('em')),
            network_error=type(exc).__name__,
        )
    return event_id


def _log_meta_result(status, event_name, event_id, *, http_status=None, events_received=None,
                     fbtrace_id=None, error_type=None, error_code=None, error_subcode=None,
                     network_error=None, test_event_code_present=False, em_present=False):
    log = logger.info if status == 'success' else logger.warning
    log(
        'Meta CAPI status=%s event_name=%s event_id=%s http_status=%s '
        'events_received=%s fbtrace_id=%s error.type=%s error.code=%s '
        'error.error_subcode=%s network_error=%s test_event_code_present=%s em_present=%s',
        status, event_name, event_id, http_status, events_received, fbtrace_id,
        error_type, error_code, error_subcode, network_error, test_event_code_present, em_present,
    )


def _post_meta_event(url, body, event_name, event_id):
    test_event_code_present = bool(body.get('test_event_code'))
    em_present = bool(body['data'][0]['user_data'].get('em'))
    try:
        response = requests.post(url, json=body, timeout=4)
    except Exception as exc:
        _log_meta_result(
            'network_error', event_name, event_id,
            network_error=type(exc).__name__, test_event_code_present=test_event_code_present,
            em_present=em_present,
        )
        return
    try:
        result = response.json()
    except ValueError:
        _log_meta_result(
            'invalid_response', event_name, event_id, http_status=response.status_code,
            test_event_code_present=test_event_code_present, em_present=em_present,
        )
        return
    error = result.get('error') if isinstance(result, dict) else None
    error_details = error if isinstance(error, dict) else {}
    response_details = result if isinstance(result, dict) else {}
    if response.ok and not error:
        status = 'success'
    else:
        status = 'meta_error'
    _log_meta_result(
        status, event_name, event_id, http_status=response.status_code,
        events_received=response_details.get('events_received'),
        fbtrace_id=response_details.get('fbtrace_id') or error_details.get('fbtrace_id'),
        error_type=error_details.get('type'), error_code=error_details.get('code'),
        error_subcode=error_details.get('error_subcode'),
        test_event_code_present=test_event_code_present, em_present=em_present,
    )


def track_page_view(request, event_id=None):
    return send_event(request, 'PageView', event_id=event_id, user_data=_event_email_user_data(request))


def track_view_content(request, product, event_id=None):
    content_id = product.sifra or str(product.pk)
    custom_data = {
        'content_ids': [content_id],
        'content_type': 'product',
        'content_name': product.naziv,
        'value': float(product.prikazna_cijena),
        'currency': CURRENCY,
        'contents': [build_content_item(content_id, 1, product.prikazna_cijena)],
    }
    return send_event(
        request, 'ViewContent', event_id=event_id,
        user_data=_event_email_user_data(request), custom_data=custom_data,
    )


def track_add_to_cart(request, product, variation=None, quantity=1, event_id=None):
    content_id = (
        (variation.sifra if variation and variation.sifra else None)
        or product.sifra
        or str(product.pk)
    )
    price = variation.prikazna_cijena if variation else product.prikazna_cijena
    name = product.naziv
    if variation:
        name = f'{product.naziv} — {variation.naziv}'
    custom_data = {
        'content_ids': [content_id],
        'content_type': 'product',
        'content_name': name,
        'value': float(price * quantity),
        'currency': CURRENCY,
        'contents': [build_content_item(content_id, quantity, price)],
    }
    return send_event(
        request, 'AddToCart', event_id=event_id,
        user_data=_event_email_user_data(request), custom_data=custom_data,
    )


def track_initiate_checkout(request, cart, event_id=None):
    items = []
    content_ids = []
    value = Decimal('0')
    for item in cart:
        content_id = item.get('sifra') or str(item['product_id'])
        quantity = item['quantity']
        price = Decimal(item['cijena'])
        value += price * quantity
        content_ids.append(content_id)
        items.append(build_content_item(content_id, quantity, price))
    custom_data = {
        'content_ids': content_ids,
        'content_type': 'product',
        'value': float(value),
        'currency': CURRENCY,
        'num_items': sum(item['quantity'] for item in cart),
        'contents': items,
    }
    return send_event(
        request, 'InitiateCheckout', event_id=event_id,
        user_data=_event_email_user_data(request), custom_data=custom_data,
    )


def track_purchase(request, order, event_id=None):
    if event_id is None:
        event_id = f'purchase-{order.broj}'

    items = []
    content_ids = []
    for stavka in order.stavke.all():
        content_id = stavka.sifra or str(stavka.artikal_id or stavka.pk)
        content_ids.append(content_id)
        items.append(build_content_item(content_id, stavka.kolicina, stavka.cijena))

    name_parts = (order.ime_prezime or '').split(None, 1)
    user_data = build_user_data(
        request,
        email=order.email,
        phone=order.telefon,
        first_name=name_parts[0] if name_parts else None,
        last_name=name_parts[1] if len(name_parts) > 1 else None,
    )
    custom_data = {
        'content_ids': content_ids,
        'content_type': 'product',
        'value': float(order.ukupno),
        'currency': CURRENCY,
        'order_id': order.broj,
        'num_items': sum(stavka.kolicina for stavka in order.stavke.all()),
        'contents': items,
    }
    return send_event(
        request,
        'Purchase',
        event_id=event_id,
        user_data=user_data,
        custom_data=custom_data,
    )
