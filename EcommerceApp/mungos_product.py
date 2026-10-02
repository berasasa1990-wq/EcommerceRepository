"""Read-only single-product preview. No HTTP client, persistence or sync hooks."""
import json
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

from django.conf import settings

from .cart import Cart

CATEGORY_CODES = {
    'štapovi': 'FishingRods',
    'mašinice': 'Reels',
    'najloni i strune': 'FishingLinesLeaders',
    'udice': 'Hooks',
    'varalice': 'Lures',
    'plovci': 'FloatsBobbers',
    'hranilice': 'Feeders',
    'mušičarski program': 'FlyFishingGear',
    'primama i mamci': 'GroundbaitsBaits',
    'dodatna oprema': 'Accessories',
    'odjeća i obuća za ribolov': 'FishingWear',
}
CATEGORY_PREFIX = 'SportRecreation_Equipment_FishingEquipment_'


def category_code(category):
    """Exact confirmed names only; nearest mapped ancestor, no fuzzy fallback."""
    visited = set()
    while category is not None and category.pk not in visited:
        visited.add(category.pk)
        suffix = CATEGORY_CODES.get(category.naziv.strip().casefold())
        if suffix:
            return CATEGORY_PREFIX + suffix
        category = category.roditelj
    return None


def _images(main, additional, issues):
    result = []
    for field, is_main in [(main, True)] + [(image.slika, False) for image in additional]:
        if not field:
            continue
        try:
            url = urljoin(settings.SITE_URL.rstrip('/') + '/', field.url)
            parsed = urlsplit(url)
            valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment)
        except (ValueError, OSError):
            valid = False
        if not valid:
            issues.append('Image URL nije siguran javni URL; izostavljen iz previewa.')
            continue
        if not any(image['imageUrl'] == url for image in result):
            result.append({'imageUrl': url, 'isMainImage': is_main})
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
    if not sku.strip():
        issues.append('Proizvod nema SKU; Mungos id nije izmišljen.')
    if not product.aktivan or product.sakriven_do_stanja:
        issues.append('Proizvod nije dostupan za kupovinu u webshopu.')
    payload = {
        'id': sku, 'sku': sku, 'name': product.naziv,
        'hasQuantities': True, 'quantityRemaining': quantities['parent'],
        'shortDescription': product.opis, 'details': product.opis,
        'productType': 'Product', 'price': float(product.prikazna_cijena),
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
    if product.barkod:
        payload['ean'] = product.barkod
    seen = {sku}
    for variation in variations:
        variant_sku = variation.sifra or ''
        if not variant_sku.strip() or variant_sku in seen:
            issues.append(f'Varijanta {variation.pk}: nedostaje ili se ponavlja SKU.')
        seen.add(variant_sku)
        price = float(variation.prikazna_cijena)
        payload['Variants'].append({
            'sku': variant_sku, 'quantityRemaining': quantities[str(variation.pk)],
            'price': price, 'sellingPrice': price,
            'images': _images(variation.slika, [], issues), 'attributes': [],
        })
        issues.append(f'Varijanta {variation.pk} ({variation.naziv}): Mungos attribute codes nisu potvrđeni.')
    return {
        'status': 'NEEDS_REVIEW' if issues else 'READY_FOR_REVIEW',
        'reviewReasons': issues,
        'carpologijaProductId': product.pk, 'name': product.naziv, 'sku': sku,
        'price': payload['price'], 'quantity': payload['quantityRemaining'],
        'category': product.kategorija.naziv if product.kategorija else None,
        'categoryCode': code, 'images': payload['images'],
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
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return json.dumps(clean(preview), ensure_ascii=False, indent=2, allow_nan=False)
