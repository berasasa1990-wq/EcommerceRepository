"""Confirmed partial PUT bodies projected from the existing outbound builder."""
from copy import deepcopy

from .mungos_payload import FIXED_VALUES, sanitize_mungos_price

PRICE_FIELDS = (
    'id', 'price', 'currencyIsoCode', 'isNegotiable', 'isFree',
    'warrantyMonthsCount', 'warrantyDescription', 'returnDaysCount',
    'returnDescription', 'sellerPaysForReturnShipping', 'exchangeAcceptable',
    'exchangeComment', 'shippmentDeliveryMethod',
)
QUANTITY_FIELDS = ('id', 'quantity')


def validate_partial_payload(payload, operation):
    fields = PRICE_FIELDS if operation == 'price' else QUANTITY_FIELDS
    if operation not in ('price', 'quantity') or not isinstance(payload, dict) or set(payload) != set(fields):
        return False
    identity = payload['id']
    if not isinstance(identity, str) or not identity.strip() or any(ord(c) < 32 for c in identity):
        return False
    if operation == 'quantity':
        return type(payload['quantity']) is int and payload['quantity'] >= 0
    if type(payload['price']) not in (int, float) or sanitize_mungos_price(payload['price']) is None:
        return False
    return all(type(payload[field]) is type(FIXED_VALUES[field]) and payload[field] == FIXED_VALUES[field]
               for field in PRICE_FIELDS if field not in ('id', 'price'))


def build_partial_payload(preview, operation):
    if operation not in ('price', 'quantity'):
        raise ValueError('Unknown Mungos partial operation.')
    source = preview.get('payload')
    if (preview.get('status') != 'READY_FOR_REVIEW' or preview.get('reviewReasons')
            or preview.get('variantCount') or not isinstance(source, dict)
            or source.get('HasVariants') or source.get('Variants')):
        return None
    if operation == 'quantity':
        payload = {'id': source.get('id'), 'quantity': source.get('quantityRemaining')}
    else:
        payload = {field: deepcopy(source.get(field)) for field in PRICE_FIELDS}
    return payload if validate_partial_payload(payload, operation) else None
