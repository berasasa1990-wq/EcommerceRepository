"""X-Express API — najava pošiljke iz postojeće Django narudžbe."""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_API_URL = 'https://api.x-express.ba/v1'
NAJAVA_PATH = '/najava/v2'
REQUEST_TIMEOUT = 20


class XExpressError(Exception):
    """Greška konfiguracije, validacije ili X-Express API-ja."""


class XExpressAlreadySent(XExpressError):
    """Narudžba je već poslana u X-Express."""


def _api_url() -> str:
    base = (getattr(settings, 'XEXPRESS_API_URL', None) or DEFAULT_API_URL).strip().rstrip('/')
    return f'{base}{NAJAVA_PATH}'


def xexpress_configured() -> bool:
    username = (getattr(settings, 'XEXPRESS_USERNAME', None) or '').strip()
    password = (getattr(settings, 'XEXPRESS_PASSWORD', None) or '').strip()
    return bool(username and password)


def _credentials() -> tuple[str, str]:
    username = (getattr(settings, 'XEXPRESS_USERNAME', None) or '').strip()
    password = (getattr(settings, 'XEXPRESS_PASSWORD', None) or '').strip()
    if not username or not password:
        raise XExpressError(
            'X-Express nije konfigurisan. Postavi XEXPRESS_USERNAME i XEXPRESS_PASSWORD u .env.'
        )
    return username, password


def _money(value) -> float:
    amount = Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return float(amount)


def _timeout() -> int:
    try:
        return max(5, int(getattr(settings, 'XEXPRESS_TIMEOUT', REQUEST_TIMEOUT) or REQUEST_TIMEOUT))
    except (TypeError, ValueError):
        return REQUEST_TIMEOUT


def order_is_pouzece(order) -> bool:
    return not bool(getattr(order, 'placeno_karticom', lambda: False)())


def build_shipment_payload(order) -> dict:
    ukupno = _money(getattr(order, 'ukupno', 0))
    pouzece = order_is_pouzece(order)
    ime = (getattr(order, 'ime_prezime', None) or '').strip()
    return {
        'sifraExt': str(getattr(order, 'broj', '') or '').strip(),
        'nazivPrim': ime,
        'adresaPrim': (getattr(order, 'adresa', None) or '').strip(),
        'pttPrim': (getattr(order, 'postanski_broj', None) or '').strip(),
        'telefonPrim': (getattr(order, 'telefon', None) or '').strip(),
        'kontaktPrim': ime,
        'opisPosiljke': 'Ribolovačka oprema',
        'brojPaketa': 1,
        'duzina': 0,
        'sirina': 0,
        'visina': 0,
        'tezina': 2,
        'uslugaSifra': 1,
        'obveznikPlacanja': 1,
        'nacinPlacanja': 9,
        'vrednostPosiljke': ukupno,
        'otkupnina': pouzece,
        'iznosOtkupnine': ukupno if pouzece else 0,
    }


def extract_sifra(data) -> str:
    if isinstance(data, list):
        for item in data:
            found = extract_sifra(item)
            if found:
                return found
        return ''
    if not isinstance(data, dict):
        return ''
    for key in ('sifra', 'Sifra', 'sifraPosiljke'):
        value = data.get(key)
        if value not in (None, ''):
            return str(value).strip()
    for nested_key in ('data', 'result', 'posiljke', 'items', 'response'):
        nested = data.get(nested_key)
        if nested is not None:
            found = extract_sifra(nested)
            if found:
                return found
    return ''


def _response_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or '').strip()
        return text[:400] if text else f'HTTP {response.status_code}'
    if isinstance(payload, dict):
        for key in ('message', 'poruka', 'error', 'greska', 'detail'):
            value = payload.get(key)
            if value:
                return str(value).strip()
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            for key in ('message', 'poruka', 'error', 'greska'):
                value = first.get(key)
                if value:
                    return str(value).strip()
        return str(first)[:400]
    return f'HTTP {response.status_code}'


def create_shipment(order) -> dict:
    """Pošalji narudžbu na POST /najava/v2 i snimi X-Express šifru na narudžbu."""
    existing = (getattr(order, 'xexpress_sifra', None) or '').strip()
    if existing:
        raise XExpressAlreadySent(
            f'Narudžba #{order.broj} je već poslana u X-Express (šifra {existing}).'
        )

    username, password = _credentials()
    payload = [build_shipment_payload(order)]
    url = _api_url()
    try:
        response = requests.post(
            url,
            json=payload,
            auth=(username, password),
            timeout=_timeout(),
        )
    except requests.Timeout as exc:
        logger.warning('X-Express timeout za narudžbu #%s', getattr(order, 'broj', ''))
        raise XExpressError('X-Express ne odgovara (timeout). Pokušaj ponovo.') from exc
    except requests.RequestException as exc:
        logger.warning('X-Express mrežna greška za #%s: %s', getattr(order, 'broj', ''), exc)
        raise XExpressError(f'X-Express mrežna greška: {exc}') from exc

    if response.status_code >= 400:
        raise XExpressError(
            f'X-Express greška ({response.status_code}): {_response_error_message(response)}'
        )

    try:
        body = response.json() if response.content else {}
    except ValueError as exc:
        raise XExpressError('X-Express je vratio neispravan odgovor (nije JSON).') from exc

    sifra = extract_sifra(body)
    if not sifra:
        raise XExpressError('X-Express nije vratio šifru pošiljke.')

    order.xexpress_sifra = sifra[:40]
    order.xexpress_poslano_at = timezone.now()
    order.save(update_fields=['xexpress_sifra', 'xexpress_poslano_at'])
    return {
        'sifra': order.xexpress_sifra,
        'payload': payload,
        'response': body,
    }
