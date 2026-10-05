"""Read-only adapter for the confirmed single-product PUT schema."""
from copy import deepcopy

from .mungos_payload import UPDATE_FIELDS, sanitize_mungos_payload


def build_mungos_update_payload(preview):
    """Adapt an existing builder preview; preserve review gates and source values.

    Returns a preview with the PUT payload. Variant bodies are deliberately
    blocked until their PUT schema is confirmed. Never mutate the CREATE preview.
    """
    result = deepcopy(preview)
    source = preview.get('payload')
    reasons = result['reviewReasons']
    if not isinstance(source, dict):
        reasons.append('CREATE preview nema validan payload za UPDATE.')
        result.update(status='NEEDS_REVIEW', payload=None)
        return result
    if preview.get('variantCount') or source.get('HasVariants') or source.get('Variants'):
        reasons.append('Mungos PUT schema za varijante nije potvrđena.')
    missing = [field for field in UPDATE_FIELDS
               if field not in source and field not in ('brandCode', 'ean')]
    if missing:
        reasons.append('Nedostaju polja potvrđene Mungos PUT scheme.')
    if preview['status'] != 'READY_FOR_REVIEW' or reasons:
        result['status'] = 'NEEDS_REVIEW'
        result['payload'] = None
        return result
    result['payload'] = {
        field: deepcopy(source.get(field, '' if field == 'ean' else None))
        for field in UPDATE_FIELDS
    }
    prices = source.get('ProductPrice')
    if isinstance(prices, dict):
        # CREATE/bulk projection is adapted to the confirmed STANDARD PUT contract.
        result['payload']['price'] = deepcopy(prices.get('Price'))
        result['payload']['sellingPrice'] = deepcopy(prices.get('SellingPrice'))
        end = prices.get('DiscountEndDate')
        result['payload']['discountEndDate'] = f'{end}T23:59:59Z' if end else None
    else:
        result['payload']['sellingPrice'] = deepcopy(source.get('sellingPrice'))
        result['payload']['discountEndDate'] = deepcopy(source.get('discountEndDate'))
    result['payload'], validation_reasons = sanitize_mungos_payload(result['payload'], 'update')
    reasons.extend(validation_reasons)
    if reasons:
        result.update(status='NEEDS_REVIEW', payload=None)
    return result
