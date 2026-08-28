"""X-Express API — najava pošiljke iz postojeće Django narudžbe."""

from __future__ import annotations

import json
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


def _api_base() -> str:
    return (getattr(settings, 'XEXPRESS_API_URL', None) or DEFAULT_API_URL).strip().rstrip('/')


def _api_url() -> str:
    return f'{_api_base()}{NAJAVA_PATH}'


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


def _norm_key(key) -> str:
    return re.sub(r'[^a-z0-9]', '', str(key).casefold())


_SIFRA_KEYS = {
    'sifra',
    'sifraposiljke',
    'brojposiljke',
    'tovarnilist',
    'brojtovarnoglista',
    'sifratovarnoglista',
    'tracking',
    'trackingnumber',
    'trackingno',
    'barcode',
    'barkod',
    'shipmentid',
    'idposiljke',
    'kodposiljke',
    'posiljkasifra',
}
_XEXPRESS_CODE = re.compile(r'^[A-Z]\d{6,}$', re.I)


def extract_sifra(data, *, ignore=()) -> str:
    """Nađi X-Express šifru u bilo kojem obliku JSON odgovora (i velika slova)."""
    skip_values = {str(x).strip() for x in ignore if x not in (None, '')}
    return _extract_sifra(data, skip_values, 0)


def _leaf_code(value, skip_values: set[str]) -> str:
    if isinstance(value, bool):
        return ''
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            return ''
        text = str(int(value)).strip()
    elif isinstance(value, str):
        text = value.strip()
    else:
        return ''
    if not text or text in skip_values or text in {'0', '-'}:
        return ''
    return text


def _is_xexpress_code(text: str) -> bool:
    return bool(text and _XEXPRESS_CODE.match(text))


def _extract_sifra(data, skip_values: set[str], depth: int) -> str:
    if depth > 8:
        return ''
    found = _leaf_code(data, skip_values)
    if found and depth == 0 and not isinstance(data, (dict, list)):
        return found
    if isinstance(data, list):
        for item in data:
            found = _extract_sifra(item, skip_values, depth + 1)
            if found:
                return found
        return ''
    if not isinstance(data, dict):
        return ''
    nested = []
    mapped = []
    for key, value in data.items():
        key_text = str(key).strip()
        nk = _norm_key(key)
        leaf = _leaf_code(value, skip_values)
        # {"0136": "X018719554"} — naša referenca je ključ, X-šifra je vrijednost
        if leaf and _is_xexpress_code(leaf):
            return leaf
        if leaf and key_text in skip_values:
            mapped.append(leaf)
        if nk in _SIFRA_KEYS or nk.endswith('sifra'):
            if leaf:
                return leaf
            if isinstance(value, (dict, list)):
                nested.append(value)
        elif isinstance(value, (dict, list)):
            nested.append(value)
    if mapped:
        return mapped[0]
    for value in nested:
        found = _extract_sifra(value, skip_values, depth + 1)
        if found:
            return found
    return ''


def _preview_body(body) -> str:
    try:
        text = json.dumps(body, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(body)
    text = _fix_api_text(text.replace('\n', ' ').strip())
    return text[:280]


def _body_error_message(body) -> str:
    if isinstance(body, dict):
        for key, value in body.items():
            nk = _norm_key(key)
            if nk in ('message', 'poruka', 'error', 'greska', 'detail') and value not in (None, '', [], {}):
                if isinstance(value, (dict, list)):
                    continue
                return _fix_api_text(str(value).strip())
        ok = None
        for key, value in body.items():
            if _norm_key(key) in ('success', 'ok'):
                ok = value
        if ok in (False, 0, '0', 'false', 'False'):
            return _body_error_message(body.get('errors') or body) or 'X-Express je vratio grešku.'
    if isinstance(body, list) and body:
        return _body_error_message(body[0])
    return ''


def _is_duplicate_error(text: str) -> bool:
    blob = (text or '').casefold()
    return any(
        token in blob
        for token in (
            'duplicate key',
            'already exists',
            'xo_posiljka_ix1',
            'već postoji',
            'vec postoji',
            'unique constraint',
        )
    )


def _lookup_existing_sifra(sifra_ext: str) -> str:
    """Ako je najava već u X-Expressu, pokušaj povući njihovu šifru po našem broju narudžbe."""
    ext = (sifra_ext or '').strip()
    if not ext:
        return ''
    username, password = _credentials()
    timeout = min(12, _timeout())
    urls = [
        f'{_api_base()}/posiljka/{ext}',
        f'{_api_base()}/posiljke/{ext}',
        f'{_api_base()}/posiljka/ext/{ext}',
        f'{_api_base()}/posiljka?sifraExt={ext}',
        f'{_api_base()}/posiljke?sifraExt={ext}',
        f'{_api_base()}/posiljka?ibp={ext}',
        f'{_api_base()}/posiljke?ibp={ext}',
    ]
    for url in urls:
        try:
            response = requests.get(url, auth=(username, password), timeout=timeout)
        except requests.RequestException:
            continue
        if response.status_code >= 400 or not response.content:
            continue
        try:
            body = response.json()
        except ValueError:
            continue
        found = extract_sifra(body, ignore={ext})
        if found:
            return found
    return ''


def _save_sifra(order, sifra: str) -> str:
    code = (sifra or '').strip()[:40]
    order.xexpress_sifra = code
    order.xexpress_poslano_at = timezone.now()
    order.save(update_fields=['xexpress_sifra', 'xexpress_poslano_at'])
    return code


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

    order_broj = str(getattr(order, 'broj', '') or '').strip()
    ignore = {order_broj, payload[0].get('sifraExt')}

    if response.status_code >= 400:
        err = _response_error_message(response)
        if _is_duplicate_error(err) or _is_duplicate_error(response.text or ''):
            found = extract_sifra(
                _safe_json(response), ignore=ignore,
            ) or _lookup_existing_sifra(order_broj)
            if not found:
                found = f'IBP-{order_broj}'
            _save_sifra(order, found)
            return {
                'sifra': order.xexpress_sifra,
                'payload': payload,
                'response': _safe_json(response),
                'duplicate': True,
            }
        raise XExpressError(
            f'X-Express greška ({response.status_code}): {err}'
        )

    try:
        body = response.json() if response.content else {}
    except ValueError as exc:
        preview = _fix_api_text((response.text or '')[:280])
        raise XExpressError(
            f'X-Express je vratio neispravan odgovor (nije JSON). {preview}'.strip()
        ) from exc

    sifra = extract_sifra(body, ignore=ignore)
    if not sifra:
        logger.warning(
            'X-Express odgovor bez šifre za #%s HTTP %s: %s',
            order_broj,
            response.status_code,
            _preview_body(body),
        )
        extra = _body_error_message(body)
        preview = _preview_body(body)
        if extra:
            raise XExpressError(f'X-Express: {extra}')
        raise XExpressError(
            'X-Express nije vratio šifru pošiljke'
            + (f': {preview}' if preview else '.')
        )

    _save_sifra(order, sifra)
    return {
        'sifra': order.xexpress_sifra,
        'payload': payload,
        'response': body,
    }


def _safe_json(response: requests.Response):
    try:
        return response.json() if response.content else {}
    except ValueError:
        return {}
