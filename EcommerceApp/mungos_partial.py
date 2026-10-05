"""Price uses full product PUT; quantity retains its confirmed partial body."""
from .mungos_payload import STANDARD_UPDATE_FIELDS, sanitize_mungos_payload
from .mungos_update import build_mungos_update_payload

PRICE_FIELDS = STANDARD_UPDATE_FIELDS
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
    _, reasons = sanitize_mungos_payload(payload, 'update')
    return not reasons


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
        payload = build_mungos_update_payload(preview)['payload']
    return payload if validate_partial_payload(payload, operation) else None
