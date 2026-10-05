"""Read-only single-product preview. No HTTP client, persistence or sync hooks."""
import json
from types import SimpleNamespace
from urllib.parse import urljoin

from django.conf import settings

from .cart import Cart
from .mungos_sale import product_price
from .mungos_payload import (
    is_mungos_image_url, sanitize_mungos_payload,
    sanitize_mungos_price,
)

from .mungos_categories import category_code


def _images(main, additional, issues):
    result = []
    for field, is_main in [(main, True)] + [(image.slika, False) for image in additional]:
        if not field:
            continue
        try:
            url = urljoin(settings.SITE_URL.rstrip('/') + '/', field.url)
            valid = is_mungos_image_url(url)
        except (ValueError, OSError):
            valid = False
        if not valid:
            issues.append('Image URL nije siguran javni URL; izostavljen iz previewa.')
            continue
        if not any(image['imageUrl'] == url for image in result):
            result.append({'imageUrl': url, 'isMainImage': not result})
    return result


def build_product_preview(product):
    """Build candidate POST /standard/product body, never send it or save models."""
    issues = []
    variations = list(product.varijacije.all())
    # An isolated, in-memory cart allows exact reuse of checkout availability.
    cart = Cart(SimpleNamespace(session={}))
    items = [None] + variations
    cart.cart = {
        str(item.pk) if item else 'parent': {
            'product_id': product.pk, 'variation_id': item.pk if item else None,
        } for item in items
    }
    quantities = cart.availability(
        products={product.pk: product}, variants={item.pk: item for item in variations},
    )
    code = category_code(product.kategorija)
    if not code:
        issues.append('Kategorija nema potvrđeno Mungos mapiranje.')
    sku = product.sifra or ''
    if not isinstance(sku, str) or not sku.strip():
        issues.append('Proizvod nema SKU; Mungos id nije izmišljen.')
    if not product.aktivan or product.sakriven_do_stanja:
        issues.append('Proizvod nije dostupan za kupovinu u webshopu.')
    selling_price = product.prikazna_cijena
    payload = {
        'id': sku, 'sku': sku, 'name': product.naziv,
        'hasQuantities': True, 'quantityRemaining': quantities['parent'],
        'shortDescription': product.opis, 'details': product.opis,
        'productType': 'Product', 'price': selling_price,
        'currencyIsoCode': 'BAM', 'isNegotiable': False, 'isFree': False,
        'warrantyMonthsCount': None, 'warrantyDescription': None,
        'returnDaysCount': None, 'returnDescription': None,
        'sellerPaysForReturnShipping': True, 'exchangeAcceptable': False,
        'exchangeComment': None, 'shippmentDeliveryMethod': 'DeliveryByMe',
        'condition': 'New', 'countryCode': 'BA', 'cityCode': 'Bijeljina',
        'streetName': None, 'postalCode': None, 'longitude': None, 'latitude': None,
        'categoryUuid': None, 'categoryCode': code, 'productAttributes': {},
        'images': _images(product.prikazna_slika, product.dodatne_slike.all(), issues),
        'HasVariants': bool(variations), 'Variants': [],
    }
    payload['ProductPrice'] = product_price(product, selling_price)
    payload['ean'] = product.barkod
    seen = {sku} if isinstance(sku, str) else set()
    for variation in variations:
        variant_sku = variation.sifra or ''
        if not isinstance(variant_sku, str) or not variant_sku.strip() or variant_sku in seen:
            issues.append(f'Varijanta {variation.pk}: nedostaje ili se ponavlja SKU.')
        if isinstance(variant_sku, str):
            seen.add(variant_sku)
        # Reuse the same numeric rule even for blocked variant diagnostics.
        price = sanitize_mungos_price(variation.prikazna_cijena)
        payload['Variants'].append({
            'sku': variant_sku, 'quantityRemaining': quantities[str(variation.pk)],
            'price': price, 'sellingPrice': price,
            'images': _images(variation.slika, [], issues), 'attributes': [],
        })
        issues.append(f'Varijanta {variation.pk} ({variation.naziv}): Mungos attribute codes nisu potvrđeni.')
    payload, validation_reasons = sanitize_mungos_payload(payload, 'create')
    issues.extend(validation_reasons)
    # A non-serializable candidate is blocked and has no outbound body.
    summary = payload or {}
    return {
        'status': 'NEEDS_REVIEW' if issues else 'READY_FOR_REVIEW',
        'reviewReasons': issues,
        'carpologijaProductId': product.pk, 'name': product.naziv, 'sku': sku,
        'price': summary.get('price'), 'quantity': summary.get('quantityRemaining'),
        'category': product.kategorija.naziv if product.kategorija else None,
        'categoryCode': code, 'images': summary.get('images', []),
        'variantCount': len(variations), 'payload': payload,
    }


def sanitized_json(preview):
    """Redact configured credential values even if present in catalog text."""
    secrets = []
    for name in dir(settings):
        if any(marker in name for marker in ('SECRET', 'PASSWORD', 'TOKEN', 'API_KEY', 'ACCESS_CODE')):
            value = getattr(settings, name, None)
            if isinstance(value, str) and value:
                secrets.append(value)

    def clean(value):
        if isinstance(value, str):
            for secret in sorted(secrets, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            return value
        if isinstance(value, dict):
            return {clean(key): clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return json.dumps(clean(preview), ensure_ascii=False, indent=2, allow_nan=False)
