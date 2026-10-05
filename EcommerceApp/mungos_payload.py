"""Shared outbound payload rules for the Mungos integration."""
from copy import deepcopy
from decimal import Decimal
import json
import math
from datetime import date
from urllib.parse import urlsplit


from .mungos_categories import CATEGORY_CODES, CATEGORY_PREFIX, CONFIRMED_CATEGORY_CODES

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
CREATE_FIELDS = tuple(field for field in UPDATE_FIELDS if field != 'brandCode') + ('HasVariants', 'Variants')

# Only values already present in the documented single-product examples.
FIXED_VALUES = {
    'currencyIsoCode': 'BAM', 'condition': 'New', 'countryCode': 'BA',
    'cityCode': 'Bijeljina', 'productType': 'Product', 'hasQuantities': True,
    'isNegotiable': False, 'isFree': False, 'warrantyMonthsCount': None,
    'warrantyDescription': None, 'returnDaysCount': None, 'returnDescription': None,
    'sellerPaysForReturnShipping': True, 'exchangeAcceptable': False,
    'exchangeComment': None, 'shippmentDeliveryMethod': 'DeliveryByMe',
    'streetName': None, 'postalCode': None, 'longitude': None, 'latitude': None,
    'productAttributes': {},
}


def sanitize_mungos_ean(value):
    """Preserve checksum-valid EAN-8/EAN-13 only; never repair source EANs."""
    if isinstance(value, str) and len(value) in (8, 13) and value.isascii() and value.isdigit():
        weighted_sum = sum(int(digit) * (3 if index % 2 == 0 else 1)
                           for index, digit in enumerate(reversed(value[:-1])))
        if (weighted_sum + int(value[-1])) % 10 == 0:
            return value
    return ''


def is_mungos_image_url(value):
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value) or '\\' in value:
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in ('http', 'https') and parsed.hostname
                    and parsed.port != 0 and not parsed.username and not parsed.password
                    and not parsed.query and not parsed.fragment and '?' not in value and '#' not in value)
    except ValueError:
        return False


def sanitize_mungos_price(value):
    """Return a JSON number or None for an invalid source price; do not invent one."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def sanitize_mungos_payload(source, operation='create'):
    """Return a detached JSON-safe candidate and blocking reasons; never save data.

    Missing descriptions/EAN and negative integer availability are safe outbound
    transformations. Invalid identity, price, schema or mappings require review.
    No checksum repair, invented attributes, URLs, or variant schema.
    """
    if operation not in ('create', 'update'):
        raise ValueError('Unknown Mungos payload operation.')
    fields = CREATE_FIELDS if operation == 'create' else UPDATE_FIELDS
    if not isinstance(source, dict):
        return None, ['Mungos payload mora biti objekt.']
    payload = deepcopy(source)
    reasons = []
    required = set(fields) - {'ean', 'shortDescription', 'details'}
    if required - payload.keys() or payload.keys() - (set(fields) | {'ProductPrice'}):
        reasons.append('Mungos payload ne odgovara potvrđenim poljima scheme.')
    payload['ean'] = sanitize_mungos_ean(payload.get('ean'))
    for field in ('id', 'sku', 'name'):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
            reasons.append(f'{field}: potreban je neprazan string bez kontrolnih znakova.')
    if payload.get('id') != payload.get('sku'):
        reasons.append('Mungos id mora odgovarati postojećem SKU-u.')
    for field in ('shortDescription', 'details'):
        value = payload.get(field)
        if value is None:
            payload[field] = ''
        elif not isinstance(value, str):
            reasons.append(f'{field}: opis mora biti string ili prazan.')
    payload['price'] = sanitize_mungos_price(payload.get('price'))
    if payload['price'] is None:
        reasons.append('price: potrebna je konačna nenegativna numerička cijena.')
    if 'ProductPrice' in payload:
        prices = payload['ProductPrice']
        expected = {'Price', 'SellingPrice', 'Currency', 'IsNegotiable', 'IsFree', 'DiscountEndDate'}
        if not isinstance(prices, dict) or set(prices) != expected:
            reasons.append('ProductPrice: neispravna potvrđena schema.')
        else:
            for field in ('Price', 'SellingPrice'):
                prices[field] = sanitize_mungos_price(prices[field])
            if (prices['Price'] is None or prices['SellingPrice'] is None
                    or prices['SellingPrice'] > prices['Price']
                    or prices['SellingPrice'] != payload['price']
                    or prices['Currency'] is not None
                    or prices['IsNegotiable'] is not False or prices['IsFree'] is not False):
                reasons.append('ProductPrice: neispravne cijene ili fixed vrijednosti.')
            end = prices['DiscountEndDate']
            if end is not None:
                try:
                    if not isinstance(end, str) or date.fromisoformat(end).isoformat() != end:
                        raise ValueError
                    if prices['Price'] == prices['SellingPrice']:
                        raise ValueError
                except (ValueError, TypeError):
                    reasons.append('ProductPrice: neispravan DiscountEndDate.')
    quantity = payload.get('quantityRemaining')
    if type(quantity) is not int:
        reasons.append('quantityRemaining: potrebna je integer količina.')
        payload['quantityRemaining'] = None
    else:
        payload['quantityRemaining'] = max(0, quantity)
    code = payload.get('categoryCode')
    if not isinstance(code, str) or code not in CONFIRMED_CATEGORY_CODES:
        reasons.append('categoryCode nema potvrđeno Mungos mapiranje.')
    # This adapter has only confirmed code mappings, never fabricated category UUIDs.
    if payload.get('categoryUuid') is not None:
        reasons.append('categoryUuid nije dio potvrđenog lokalnog mapiranja.')
    for field, expected in FIXED_VALUES.items():
        value = payload.get(field)
        if type(value) is not type(expected) or value != expected:
            reasons.append(f'{field}: vrijednost odstupa od potvrđenog primjera.')
    if operation == 'update' and payload.get('brandCode') is not None:
        reasons.append('brandCode nema potvrđeno mapiranje.')
    if payload.get('HasVariants') or payload.get('Variants'):
        reasons.append('Mungos schema za varijante nije potpuno potvrđena.')
    if operation == 'create' and (payload.get('HasVariants') is not False or payload.get('Variants') != []):
        reasons.append('CREATE varijante zahtijevaju pregled.')
    images = payload.get('images')
    if not isinstance(images, list):
        reasons.append('images mora biti lista postojećih URL-ova.')
        payload['images'] = []
    else:
        clean_images = []
        for row in images:
            if (not isinstance(row, dict) or set(row) != {'imageUrl', 'isMainImage'}
                    or not is_mungos_image_url(row.get('imageUrl')) or type(row.get('isMainImage')) is not bool):
                reasons.append('Slika nema validan postojeći URL ili potvrđenu schemu.')
                continue
            if not any(item['imageUrl'] == row['imageUrl'] for item in clean_images):
                clean_images.append({'imageUrl': row['imageUrl'], 'isMainImage': not clean_images})
        payload['images'] = clean_images
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        return None, reasons + ['Payload nije moguće sigurno serijalizovati kao JSON.']
    return payload, list(dict.fromkeys(reasons))
