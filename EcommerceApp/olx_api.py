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
OLX_JPEG_QUALITY = 92
DEFAULT_OLX_CATEGORY_ID = 1260  # Ostali ribolovni pribor
DEFAULT_OLX_COUNTRY_ID = 49
DEFAULT_OLX_CITY_ID = 77  # Bijeljina (CarpologijaBH profil)


def _olx_jpeg_buffer(image, *, stem='olx-image'):
    """Puna rezolucija — samo AVIF/WEBP konvertujemo u JPEG bez skaliranja."""
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, format='JPEG', quality=OLX_JPEG_QUALITY, optimize=True)
    buffer.seek(0)
    return f'{stem}.jpg', buffer, 'image/jpeg'


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
                return _olx_jpeg_buffer(image, stem=file_path.stem)
        except OSError:
            logger.warning('OLX slika nije čitljiva: %s', file_path)
            return None

    def _upload_image_file(self, listing_id, upload_name, handle, mime):
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
            logger.warning(
                'OLX upload slike nije uspio: %s',
                response.text[:300],
            )
            return []
        try:
            batch = response.json()
        except ValueError:
            return []
        if isinstance(batch, list):
            return batch
        return []

    def upload_image_url(self, listing_id, image_url):
        try:
            batch = self._request(
                'POST',
                f'/listings/{listing_id}/image-upload',
                json={'image_url': image_url},
            )
        except OlxApiError:
            logger.warning('OLX upload slike preko URL-a nije uspio: %s', image_url)
            return []
        if isinstance(batch, list):
            return batch
        return []

    def upload_images(self, listing_id, image_paths, *, image_urls=None):
        uploaded = []
        for path in image_paths:
            prepared = self._uploadable_image(path)
            if not prepared:
                continue
            upload_name, handle, mime = prepared
            uploaded.extend(self._upload_image_file(listing_id, upload_name, handle, mime))
        for image_url in image_urls or []:
            uploaded.extend(self.upload_image_url(listing_id, image_url))
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

    def list_conversations(self, *, page=1):
        return self._request('GET', '/conversations', params={'page': page})

    def get_conversation_messages(self, conversation_id, *, page=1):
        return self._request(
            'GET',
            f'/conversations/{conversation_id}/messages',
            params={'page': page},
        )

    def mark_conversation_seen(self, conversation_id):
        return self._request('POST', f'/conversations/{conversation_id}/seen', json={})


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
    """
    Cijena za OLX/Pik — ista kao na sajtu (2 decimale).
    Ne zaokružuj na cijeli broj (4.50 KM ostaje 4.5, ne 5).
    """
    price = product.prikazna_cijena
    if price is None:
        return 0
    quantized = Decimal(price).quantize(Decimal('0.01'))
    # float zadržava decimale u JSON-u; int bi zaokružio 4.5 → 5
    as_float = float(quantized)
    if as_float == int(as_float):
        return int(as_float)
    return as_float


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


def _absolute_media_url(file_field):
    if not file_field:
        return None
    try:
        url = file_field.url
    except (ValueError, OSError):
        return None
    if not url:
        return None
    if url.startswith(('http://', 'https://')):
        return url
    site_url = settings.SITE_URL.rstrip('/')
    return f'{site_url}{url}'


def _product_image_sources(product, *, max_images=8):
    paths = []
    urls = []
    if product.slika:
        try:
            path = product.slika.path
            if Path(path).is_file():
                paths.append(path)
            else:
                media_url = _absolute_media_url(product.slika)
                if media_url:
                    urls.append(media_url)
        except (ValueError, OSError):
            media_url = _absolute_media_url(product.slika)
            if media_url:
                urls.append(media_url)
    for extra in product.dodatne_slike.all():
        added = False
        try:
            path = extra.slika.path
            if Path(path).is_file():
                paths.append(path)
                added = True
        except (ValueError, OSError):
            pass
        if not added:
            media_url = _absolute_media_url(extra.slika)
            if media_url:
                urls.append(media_url)
        if len(paths) + len(urls) >= max_images:
            break
    return paths[:max_images], urls[:max_images]


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
    """CarpologijaBH shop — uvijek ribolovna kategorija (suggest često pogriješi, npr. Mobiteli)."""
    return client.default_category_id


def _update_listing_payload(product):
    return {
        'title': _olx_safe_title(product.naziv),
        'description': _product_description(product),
        'short_description': (product.seo_description or product.naziv)[:250],
        'price': _product_price(product),
        'available': bool(product.na_stanju),
    }


def _create_listing_for_product(client, product):
    category_id = _resolve_olx_category_id(client, product)
    attributes = client.build_attributes(category_id)
    payload = _listing_payload(product, category_id=category_id, attributes=attributes)
    try:
        listing = client.create_listing(payload)
    except OlxApiError as exc:
        if exc.status != 422:
            raise
        payload = _listing_payload(
            product,
            category_id=client.default_category_id,
            attributes=client.build_attributes(client.default_category_id),
        )
        listing = client.create_listing(payload)
    return int(listing['id'])


def _sync_listing_images(client, listing_id, product):
    image_paths, image_urls = _product_image_sources(product)
    if not image_paths and not image_urls:
        return
    uploaded = client.upload_images(listing_id, image_paths, image_urls=image_urls)
    if uploaded:
        main_id = uploaded[0].get('id')
        if main_id:
            client.set_main_image(listing_id, main_id)


def publish_product_to_olx(product):
    """Kreira ili ažurira oglas na OLX.ba / Pik profilu."""
    client = OlxClient.from_settings()
    listing_id = product.olx_listing_id

    if listing_id:
        try:
            client.update_listing(listing_id, _update_listing_payload(product))
        except OlxApiError as exc:
            if exc.status == 404:
                logger.warning(
                    'OLX listing %s ne postoji, kreiram novi za %s',
                    listing_id,
                    product.slug,
                )
                listing_id = None
            elif exc.status == 422:
                logger.warning(
                    'OLX update %s nije uspio (422), kreiram novi oglas za %s: %s',
                    listing_id,
                    product.slug,
                    exc,
                )
                listing_id = None
            else:
                raise

    if not listing_id:
        listing_id = _create_listing_for_product(client, product)

    _sync_listing_images(client, listing_id, product)

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


def olx_chat_configured():
    return bool(getattr(settings, 'OLX_API_TOKEN', ''))


def _olx_listing_url(listing):
    if not listing:
        return ''
    listing_id = listing.get('id')
    slug = (listing.get('slug') or '').strip('/')
    if listing_id and slug:
        return f'https://olx.ba/artikal/{slug}/{listing_id}'
    return ''


def _olx_plain_text(value):
    text = strip_tags(value or '')
    return re.sub(r'\s+', ' ', text.replace('\r', ' ')).strip()


def _olx_timestamp(value):
    if not value:
        return None
    from django.utils import timezone as dj_timezone

    return dj_timezone.datetime.fromtimestamp(int(value), tz=dj_timezone.get_current_timezone())


def _olx_listing_display_price(listing):
    if not listing:
        return ''
    if listing.get('display_price'):
        return listing['display_price']
    price = listing.get('price')
    if price is not None:
        return f'{price} KM'
    return ''


def serialize_olx_conversation(conversation):
    sender = conversation.get('sender') or {}
    listing = conversation.get('listing') or {}
    last_message = conversation.get('last_message') or {}
    username = sender.get('username') or '—'
    is_pik_system = username == 'PIK' or sender.get('id') in (0, None)
    listing_title = listing.get('title') or ''
    if listing_title:
        subject = listing_title
        subject_kind = 'Oglas'
    elif is_pik_system:
        subject = 'Sistemska obavijest PIK'
        subject_kind = 'PIK'
    else:
        subject = 'Opća poruka'
        subject_kind = 'Poruka'
    return {
        'id': conversation.get('id'),
        'username': username,
        'avatar': sender.get('avatar') or '',
        'initial': (username[:1] or '?').upper(),
        'is_pik_system': is_pik_system,
        'unread': bool(conversation.get('unread_messages')) or not conversation.get('seen', True),
        'listing_title': listing_title,
        'listing_url': _olx_listing_url(listing),
        'listing_image': listing.get('image') or '',
        'listing_price': _olx_listing_display_price(listing),
        'subject': subject,
        'subject_kind': subject_kind,
        'preview': _olx_plain_text(last_message.get('content'))[:160],
        'updated_at': _olx_timestamp(conversation.get('updated_at')),
    }


def serialize_olx_message(message, *, shop_username='CarpologijaBH', listing_url=''):
    sender = message.get('sender') or {}
    username = sender.get('username') or '—'
    listing_data = message.get('data') or {}
    msg_type = message.get('type') or 'text'
    listing_title = listing_data.get('title') or ''
    listing_price = listing_data.get('price')
    if listing_price is not None and not isinstance(listing_price, str):
        listing_price = f'{listing_price} KM'
    return {
        'id': message.get('id'),
        'type': msg_type,
        'content': _olx_plain_text(message.get('content')),
        'username': username,
        'avatar': sender.get('avatar') or '',
        'initial': (username[:1] or '?').upper(),
        'is_mine': username == shop_username,
        'is_system': username == 'PIK',
        'has_listing_context': msg_type == 'listing' and bool(listing_title),
        'listing_title': listing_title,
        'listing_price': listing_price or '',
        'listing_image': listing_data.get('image') or '',
        'listing_url': listing_url if msg_type == 'listing' else '',
        'created_at': _olx_timestamp(message.get('created_at')),
    }


def fetch_olx_conversations(*, page=1, customers_only=False):
    client = OlxClient.from_settings()
    payload = client.list_conversations(page=page)
    conversations = [
        serialize_olx_conversation(item)
        for item in (payload.get('data') or [])
    ]
    if customers_only:
        conversations = [item for item in conversations if not item['is_pik_system']]
    unread_count = sum(1 for item in conversations if item['unread'])
    return {
        'conversations': conversations,
        'unread_count': unread_count,
    }


def fetch_olx_conversation_thread(conversation_id, *, mark_seen=True, listing_url=''):
    client = OlxClient.from_settings()
    if mark_seen:
        try:
            client.mark_conversation_seen(conversation_id)
        except OlxApiError:
            logger.warning('OLX mark seen nije uspio za konverzaciju %s', conversation_id)

    messages_payload = client.get_conversation_messages(conversation_id)
    messages = [
        serialize_olx_message(item, listing_url=listing_url)
        for item in (messages_payload.get('data') or [])
    ]
    messages.sort(key=lambda item: item.get('created_at') or 0)
    return {'messages': messages}