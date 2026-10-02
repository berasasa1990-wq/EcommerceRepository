"""Read-only adapter for the confirmed single-product PUT schema."""
from copy import deepcopy


UPDATE_FIELDS = (
    'id', 'name', 'categoryUuid', 'categoryCode', 'brandCode',
    'hasQuantities', 'quantityRemaining', 'shortDescription', 'details',
    'productType', 'price', 'currencyIsoCode', 'isNegotiable', 'isFree',
    'sku', 'ean', 'warrantyMonthsCount', 'warrantyDescription',
    'returnDaysCount', 'returnDescription', 'sellerPaysForReturnShipping',
    'exchangeAcceptable', 'exchangeComment', 'shippmentDeliveryMethod',
    'condition', 'countryCode', 'cityCode', 'streetName', 'postalCode',
    'longitude', 'latitude', 'productAttributes', 'images',
)


def build_mungos_update_payload(preview):
    """Adapt an existing builder preview; preserve review gates and source values.

    Returns a preview with the PUT payload. Variant bodies are deliberately
    blocked until their PUT schema is confirmed. Never mutate the CREATE preview.
    """
    result = deepcopy(preview)
    source = preview['payload']
    reasons = result['reviewReasons']
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
    return result
