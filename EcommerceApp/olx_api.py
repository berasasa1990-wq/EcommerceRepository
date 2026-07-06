import io
import logging
import mimetypes
import re
import unicodedata
from decimal import Decimal
from pathlib import Path

import requests
from PIL import Image
from django.conf import settings
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

OLX_API_BASE = 'https://api.olx.ba'
DEFAULT_OLX_CATEGORY_ID = 1260  # Ostali ribolovni pribor
DEFAULT_OLX_COUNTRY_ID = 49
DEFAULT_OLX_CITY_ID = 77  # Bijeljina (CarpologijaBH profil)


class OlxApiError(Exception):
    def __init__(self, message, status=None, details=None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


class OlxClient:
    def __init__(self, token, *, city_id=None, country_id=None, default_category_id=None):
        self.token = (token or '').strip()
        if not self.token:
            raise OlxApiError('OLX API token nije postavljen.')
        self.city_id = int(city_id or DEFAULT_OLX_CITY_ID)
        self.country_id = int(country_id or DEFAULT_OLX_COUNTRY_ID)
        self.default_category_id = int(default_category_id or DEFAULT_OLX_CATEGORY_ID)
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.token}',
            'Accept': 'application/json',
            'User-Agent': 'opremazaribolov-olx-sync/1.0',
        })

    @classmethod
    def from_settings(cls):
        return cls(
            settings.OLX_API_TOKEN,
            city_id=settings.OLX_CITY_ID,
            country_id=settings.OLX_COUNTRY_ID,
            default_category_id=settings.OLX_DEFAULT_CATEGORY_ID,
        )

    def _request(self, method, path, **kwargs):
        url = f'{OLX_API_BASE}{path}'
        try:
            response = self.session.request(method, url, timeout=60, **kwargs)
        except requests.RequestException as exc:
            raise OlxApiError(f'OLX API nije dostupan: {exc}') from exc

        if response.status_code >= 400:
            details = {}
            try:
                payload = response.json()
                err = payload.get('error') or payload
                message = err.get('message') or response.text or 'OLX API greška'
                details = err.get('errors') or err
            except ValueError:
                message = response.text or 'OLX API greška'
            raise OlxApiError(message, status=response.status_code, details=details)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {'raw': response.text}

    def suggest_category_id(self, keyword):
        try:
            data = self._request('GET', '/categories/suggest', params={'keyword': keyword})
            items = data.get('data') or []
            if items:
                return int(items[0]['id'])
        except OlxApiError:
            logger.warning('OLX suggest kategorije nije uspio za %r, koristim default.', keyword)
        return self.default_category_id

    def category_attributes(self, category_id):
        try:
            data = self._request('GET', f'/categories/{category_id}/attributes')
            return data.get('data') or []
        except OlxApiError:
            logger.warning('OLX atributi kategorije %s nisu dostupni.', category_id)
            return []

    def build_attributes(self, category_id):
        attributes = []
        for attr in self.category_attributes(category_id):
            if not attr.get('required'):
                continue
            value = self._default_attribute_value(attr)
            if value is not None:
                attributes.append({'id': attr['id'], 'value': value})
        return attributes

    @staticmethod
    def _default_attribute_value(attr):
        options = attr.get('options') or []
        input_type = attr.get('input_type') or ''
        if input_type == 'select' and options:
            return str(options[0])
        if input_type == 'select-range' and options:
            return str(options[0])
        if input_type == 'checkbox':
            return '0'
        return '-'

    def create_listing(self, payload):
        return self._request('POST', '/listings', json=payload)

    def update_listing(self, listing_id, payload):
        return self._request('PUT', f'/listings/{listing_id}', json=payload)

    @staticmethod
    def _uploadable_image(file_path):
        file_path = Path(file_path)
        if not file_path.is_file():
            return None
        suffix = file_path.suffix.lower()
        if suffix in {'.jpg', '.jpeg', '.png', '.webp'}:
            mime, _ = mimetypes.guess_type(file_path.name)
            return file_path.name, file_path.open('rb'), mime or 'image/jpeg'
        try:
            with Image.open(file_path) as image:
                converted = image.convert('RGB')
                buffer = io.BytesIO()
                converted.save(buffer, format='JPEG', quality=88)
                buffer.seek(0)
                upload_name = f'{file_path.stem}.jpg'
                return upload_name, buffer, 'image/jpeg'
        except OSError:
            logger.warning('OLX slika nije čitljiva: %s', file_path)
            return None

    def upload_images(self, listing_id, image_paths):
        uploaded = []
        for path in image_paths:
            prepared = self._uploadable_image(path)
            if not prepared:
                continue
            upload_name, handle, mime = prepared
            try:
                response = self.session.post(
                    f'{OLX_API_BASE}/listings/{listing_id}/image-upload',
                    files={'images[]': (upload_name, handle, mime)},
                    timeout=120,
                )
            finally:
                if hasattr(handle, 'close'):
                    handle.close()
            if response.status_code >= 400:
                logger.warning('OLX upload slike nije uspio za %s: %s', path, response.text[:300])
                continue
            try:
                batch = response.json()
            except ValueError:
                continue
            if isinstance(batch, list):
                uploaded.extend(batch)
        return uploaded

    def set_main_image(self, listing_id, image_id):
        try:
            self._request(
                'PUT',
                f'/listings/{listing_id}/image-main',
                json={'imageId': image_id},
            )
        except OlxApiError:
            logger.warning('OLX postavljanje glavne slike nije uspjelo za listing %s', listing_id)

    def publish_listing(self, listing_id):
        return self._request('POST', f'/listings/{listing_id}/publish')

    def activate_listing(self, listing_id):
        return self._request('POST', f'/listings/{listing_id}/activate')

    def listing_public_url(self, listing_id, slug):
        slug = (slug or '').strip('/') or 'artikal'
        return f'https://olx.ba/artikal/{slug}/{listing_id}'


def _olx_safe_title(text):
    """OLX dozvoljava latinicu + čćžšđ, bez specijalnih znakova (npr. ′)."""
    text = unicodedata.normalize('NFKC', (text or '').strip())
    replacements = {
        '′': "'", ''': "'", ''': "'", '"': '"', '"': '"',
        '–': '-', '—': '-', '×': 'x', '°': '',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    allowed_extra = set('čćžšđČĆŽŠĐ')
    cleaned = []
    for char in text:
        if char.isalnum() or char in " -_/.,()+&'":
            cleaned.append(char)
        elif char in allowed_extra:
            cleaned.append(char)
        elif char.isspace():
            cleaned.append(' ')
    result = re.sub(r'\s+', ' ', ''.join(cleaned)).strip()
    return result[:200] or 'Artikal'


def _product_price(product):
    price = product.prikazna_cijena
    if price is None:
        return 0
    return int(Decimal(price).quantize(Decimal('1')))


def _product_description(product):
    parts = []
    if product.opis:
        text = strip_tags(product.opis).strip()
        if text:
            parts.append(text)
    site_url = settings.SITE_URL.rstrip('/')
    parts.append(f'Kupovina na sajtu: {site_url}{product.get_absolute_url()}')
    if product.sifra:
        parts.append(f'Šifra: {product.sifra}')
    if product.barkod:
        parts.append(f'Barkod: {product.barkod}')
    if product.brend:
        parts.append(f'Brend: {product.brend.naziv}')
    return '\n\n'.join(parts)


def _product_image_paths(product, *, max_images=8):
    paths = []
    if product.slika:
        try:
            path = product.slika.path
            if Path(path).is_file():
                paths.append(path)
        except (ValueError, OSError):
            pass
    for extra in product.dodatne_slike.all():
        try:
            path = extra.slika.path
            if Path(path).is_file():
                paths.append(path)
        except (ValueError, OSError):
            continue
        if len(paths) >= max_images:
            break
    return paths[:max_images]


def _listing_payload(product, *, category_id, attributes):
    payload = {
        'title': _olx_safe_title(product.naziv),
        'description': _product_description(product),
        'short_description': (product.seo_description or product.naziv)[:250],
        'price': _product_price(product),
        'listing_type': 'sell',
        'state': 'new',
        'available': bool(product.na_stanju),
        'country_id': settings.OLX_COUNTRY_ID,
        'city_id': settings.OLX_CITY_ID,
        'category_id': category_id,
        'attributes': attributes,
    }
    sku = (product.sifra or '')[:100]
    if sku:
        payload['sku_number'] = sku
    return payload


def _resolve_olx_category_id(client, product):
    """Za CarpologijaBH koristimo ribolovnu kategoriju, ne auto-suggest (često pogriješi)."""
    hints = [product.naziv]
    if product.kategorija:
        hints.append(product.kategorija.naziv)
    for hint in hints:
        category_id = client.suggest_category_id(hint)
        if category_id == client.default_category_id:
            return category_id
        try:
            attrs = client.category_attributes(category_id)
            names = ' '.join(
                (a.get('name') or '') + ' ' + (a.get('display_name') or '')
                for a in attrs
            ).lower()
            if any(
                word in names
                for word in ('ribolov', 'stap', 'masinic', 'varalic', 'pecanje')
            ) or category_id == client.default_category_id:
                return category_id
        except OlxApiError:
            continue
    return client.default_category_id


def publish_product_to_olx(product):
    """Kreira ili ažurira oglas na OLX.ba / Pik profilu."""
    client = OlxClient.from_settings()
    category_id = _resolve_olx_category_id(client, product)
    attributes = client.build_attributes(category_id)
    payload = _listing_payload(product, category_id=category_id, attributes=attributes)

    if product.olx_listing_id:
        listing = client.update_listing(product.olx_listing_id, payload)
        listing_id = product.olx_listing_id
    else:
        try:
            listing = client.create_listing(payload)
        except OlxApiError as exc:
            if exc.status == 422:
                category_id = client.default_category_id
                attributes = client.build_attributes(category_id)
                payload = _listing_payload(product, category_id=category_id, attributes=attributes)
                listing = client.create_listing(payload)
            else:
                raise
        listing_id = int(listing['id'])

    image_paths = _product_image_paths(product)
    if image_paths:
        uploaded = client.upload_images(listing_id, image_paths)
        if uploaded:
            main_id = uploaded[0].get('id')
            if main_id:
                client.set_main_image(listing_id, main_id)

    publish_result = client.publish_listing(listing_id)
    activate_result = client.activate_listing(listing_id)
    listing = client._request('GET', f'/listings/{listing_id}')
    slug = listing.get('slug') or product.slug
    status = listing.get('status') or activate_result.get('status') or publish_result.get('status')
    return {
        'id': listing_id,
        'slug': slug,
        'url': client.listing_public_url(listing_id, slug),
        'status': status,
        'activated': bool(activate_result.get('success')),
    }