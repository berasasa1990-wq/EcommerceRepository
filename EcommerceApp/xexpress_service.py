"""X-Express API — najava pošiljke iz postojeće Django narudžbe."""

from __future__ import annotations

import logging
import re
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


def _int_setting(name: str, default: int) -> int:
    raw = getattr(settings, name, None)
    if raw in (None, ''):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _fix_api_text(text: str) -> str:
    """API ponekad vrati UTF-8 pročitan kao Latin-1 (poÅ¡iljke → pošiljke)."""
    if not text:
        return text
    if 'Å' in text or 'Ä' in text or 'Ã' in text:
        try:
            return text.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    return text


def order_is_pouzece(order) -> bool:
    return not bool(getattr(order, 'placeno_karticom', lambda: False)())


_PTT_RE = re.compile(r'\b(\d{5})\b')
_DIACRITICS = str.maketrans('čćžšđČĆŽŠĐ', 'cczsdCCZSD')

# Glavni PTT po gradu — kad na narudžbi nema broja, uzmi iz grada.
_BIH_PTT = {
    'banja luka': '78000',
    'banjaluka': '78000',
    'banovici': '75280',
    'bihac': '77000',
    'bileca': '89230',
    'bosanska krupa': '77240',
    'bosanski petrovac': '77250',
    'brcko': '76100',
    'bugojno': '70230',
    'busovaca': '72260',
    'cazin': '77220',
    'capljina': '88300',
    'celinac': '78240',
    'citluk': '88260',
    'derventa': '74400',
    'doboj': '74000',
    'donji vakuf': '70220',
    'foca': '73300',
    'fojnica': '71270',
    'gorazde': '73000',
    'gornji vakuf': '70240',
    'gradacac': '76250',
    'gradiska': '78400',
    'grude': '88340',
    'hadzici': '71240',
    'ilidza': '71210',
    'istocno sarajevo': '71123',
    'jajce': '70101',
    'kakanj': '72240',
    'kalesija': '75260',
    'kiseljak': '71250',
    'konjic': '88400',
    'livno': '80101',
    'ljubuski': '88320',
    'lukavac': '75300',
    'maglaj': '74250',
    'modrica': '74480',
    'mostar': '88000',
    'mrkonjic grad': '70260',
    'neum': '79400',
    'novi grad': '79220',
    'novi travnik': '72290',
    'orasje': '76270',
    'pale': '71420',
    'posusje': '88240',
    'prijedor': '79101',
    'prnjavor': '78430',
    'sanski most': '79260',
    'sarajevo': '71000',
    'srebrenik': '75350',
    'srebrenica': '75430',
    'stolac': '88360',
    'tesanj': '74260',
    'teslic': '74270',
    'tomislavgrad': '80240',
    'travnik': '72270',
    'trebinje': '89101',
    'tuzla': '75000',
    'velika kladusa': '77230',
    'visoko': '71300',
    'vitez': '72250',
    'vogosca': '71320',
    'zavidovici': '72220',
    'zenica': '72000',
    'zivinice': '75270',
    'zvornik': '75400',
    'siroki brijeg': '88220',
}


def _norm_place(text: str) -> str:
    return ' '.join((text or '').translate(_DIACRITICS).casefold().split())


def _digits_ptt(*parts: str) -> str:
    for part in parts:
        match = _PTT_RE.search(part or '')
        if match:
            return match.group(1)
    return ''


def _ptt_from_city(grad: str) -> str:
    key = _norm_place(grad)
    if not key:
        return ''
    if key in _BIH_PTT:
        return _BIH_PTT[key]
    for name, ptt in _BIH_PTT.items():
        if name in key or key in name:
            return ptt
    return ''


def _clean_city(grad: str) -> str:
    text = (grad or '').strip()
    text = _PTT_RE.sub('', text)
    return ' '.join(text.split())


def recipient_from_order(order) -> dict:
    """Ime, telefon, adresa, grad, PTT i ukupno s narudžbe."""
    ime = (getattr(order, 'ime_prezime', None) or '').strip()
    telefon = (getattr(order, 'telefon', None) or '').strip()
    adresa = (getattr(order, 'adresa', None) or '').strip()
    grad_raw = (getattr(order, 'grad', None) or '').strip()
    ptt_raw = (getattr(order, 'postanski_broj', None) or '').strip()
    ptt = _digits_ptt(ptt_raw, adresa, grad_raw) or _ptt_from_city(grad_raw)
    grad = _clean_city(grad_raw) or _clean_city(adresa)
    return {
        'ime': ime,
        'telefon': telefon,
        'adresa': adresa,
        'grad': grad,
        'ptt': ptt,
        'ukupno': _money(getattr(order, 'ukupno', 0)),
    }


def _missing_recipient_fields(data: dict) -> list[str]:
    missing = []
    if not data['ime']:
        missing.append('ime i prezime')
    if not data['telefon']:
        missing.append('telefon')
    if not data['adresa']:
        missing.append('adresa')
    if not data['ptt']:
        missing.append('poštanski broj')
    return missing


def build_shipment_payload(order) -> dict:
    dest = recipient_from_order(order)
    missing = _missing_recipient_fields(dest)
    if missing:
        raise XExpressError(
            'Na narudžbi fali: ' + ', '.join(missing) + '. Dopuni podatke pa pošalji ponovo.'
        )
    pouzece = order_is_pouzece(order)
    ukupno = dest['ukupno']
    ime = dest['ime']
    payload = {
        'sifraExt': str(getattr(order, 'broj', '') or '').strip(),
        'nazivPrim': ime,
        'adresaPrim': dest['adresa'],
        'mjestoPrim': dest['grad'],
        'pttPrim': dest['ptt'],
        'telefonPrim': dest['telefon'],
        'kontaktPrim': ime,
        'opisPosiljke': 'Ribolovačka oprema',
        'brojPaketa': 1,
        'duzina': 0,
        'sirina': 0,
        'visina': 0,
        'tezina': 2,
        'uslugaSifra': 1,
        # 1 = pošiljalac. 9 = po računu — API 420: za ovaj tip najave to nije dozvoljeno.
        'obveznikPlacanja': _int_setting('XEXPRESS_OBVEZNIK_PLACANJA', 1),
        'nacinPlacanja': _int_setting('XEXPRESS_NACIN_PLACANJA', 1),
        'vrednostPosiljke': ukupno,
        'otkupnina': pouzece,
        'iznosOtkupnine': ukupno if pouzece else 0,
    }
    if dest['grad']:
        payload['mestoPrim'] = dest['grad']
    return payload


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
        return _fix_api_text(text[:400]) if text else f'HTTP {response.status_code}'
    if isinstance(payload, dict):
        for key in ('message', 'poruka', 'error', 'greska', 'detail'):
            value = payload.get(key)
            if value:
                return _fix_api_text(str(value).strip())
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            for key in ('message', 'poruka', 'error', 'greska'):
                value = first.get(key)
                if value:
                    return _fix_api_text(str(value).strip())
        return _fix_api_text(str(first)[:400])
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
