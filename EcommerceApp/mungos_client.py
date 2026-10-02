"""Manual Mungos calls; no persistence or automatic synchronization."""
import json
from uuid import UUID
from urllib.parse import urlsplit

import requests
from django.conf import settings

from .mungos_payload import sanitize_mungos_payload


class MungosError(Exception):
    """Only fixed, credential-free messages may be exposed to the operator."""

    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.http_status = http_status


def validate_product_uuid(value):
    """Require canonical hyphenated UUID text; never accept URL/path fragments."""
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError
    except (ValueError, AttributeError):
        raise MungosError('NOT_SENT | Mungos UUID mora biti validan UUID format.') from None
    return value


class MungosClient:
    LIVENESS_PATH = '/Liveness/check/hello'
    PRODUCT_PATH = '/standard/product'
    MAX_RESPONSE_BYTES = 65536
    TIMEOUT = (5, 10)  # Connect and socket read timeout, in seconds.

    def __init__(self):
        if not settings.MUNGOS_ENABLED:
            raise MungosError('Mungos je isključen: postavite MUNGOS_ENABLED=true.')
        configured_url = settings.MUNGOS_BASE_URL
        if not isinstance(configured_url, str):
            raise MungosError('MUNGOS_BASE_URL mora biti validan HTTPS URL.')
        base_url = configured_url.strip().rstrip('/')
        try:
            parsed = urlsplit(base_url)
            valid = (
                parsed.scheme == 'https' and parsed.hostname and parsed.port != 0
                and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and not any(char.isspace() for char in base_url)
                and not any(char in base_url for char in ('?', '#', '\\'))
            )
        except ValueError:
            valid = False
        if not valid:
            raise MungosError('MUNGOS_BASE_URL mora biti validan HTTPS URL bez credentials, query ili fragmenta.')
        api_key = settings.MUNGOS_API_KEY
        if not isinstance(api_key, str) or not api_key.strip():
            raise MungosError('MUNGOS_API_KEY nije postavljen.')
        if any(ord(char) < 32 or ord(char) > 126 for char in api_key) or api_key != api_key.strip():
            raise MungosError('MUNGOS_API_KEY ima neispravan format za HTTP header.')
        access_code = settings.MUNGOS_ECOMMERCE_ACCESS_CODE
        if (not isinstance(access_code, str)
                or any(ord(char) < 32 or ord(char) > 126 for char in access_code)
                or access_code != access_code.strip()):
            raise MungosError('MUNGOS_ECOMMERCE_ACCESS_CODE ima neispravan format za HTTP header.')
        self._base_url = base_url
        self._url = base_url + self.LIVENESS_PATH
        self._api_key = api_key
        self._access_code = access_code

    def send_product(self, payload):
        """Exactly one staging POST. Never retry an ambiguous remote write."""
        return self._write_product('post', self.PRODUCT_PATH, payload)

    def update_product(self, mungos_uuid, payload):
        """Exactly one full staging PUT to an existing UUID, with no create fallback."""
        mungos_uuid = validate_product_uuid(mungos_uuid)
        return self._write_product('put', self.PRODUCT_PATH + '/' + mungos_uuid, payload)

    def _write_product(self, method, path, payload):
        if urlsplit(self._base_url).hostname != 'staging.mungos.ba':
            raise MungosError('Slanje je dozvoljeno samo na staging.mungos.ba.')
        if not self._access_code:
            raise MungosError('STAGING slanje zahtijeva MUNGOS_ECOMMERCE_ACCESS_CODE.')
        payload, reasons = sanitize_mungos_payload(payload, 'create' if method == 'post' else 'update')
        if reasons:
            raise MungosError('NOT_SENT | Mungos payload zahtijeva pregled; nema HTTP-a.')
        try:
            with requests.Session() as session:
                with getattr(session, method)(
                    self._base_url + path,
                    json=payload,
                    headers={'X-Api-Key': self._api_key,
                             'ecommerceaccesscode': self._access_code},
                    timeout=self.TIMEOUT, allow_redirects=False, stream=True,
                ) as response:
                    status = response.status_code
                    self.retry_after = response.headers.get('Retry-After')
                    body = bytearray()
                    truncated = False
                    try:
                        for chunk in response.iter_content(chunk_size=4096):
                            remaining = self.MAX_RESPONSE_BYTES - len(body)
                            body.extend(chunk[:remaining])
                            if len(chunk) > remaining:
                                truncated = True
                                break
                    except requests.RequestException:
                        raise MungosError(
                            f'UNKNOWN_REMOTE_STATE | HTTP status: {status} | '
                            f'Čitanje odgovora nije uspjelo; nema retryja. Provjerite Mungos prije novog {method.upper()}-a.',
                            http_status=status,
                        ) from None
                    decoded = body.decode('utf-8', errors='replace')
                    try:
                        result = json.loads(decoded)
                    except ValueError:
                        result = decoded
                    # Redact values AND dictionary keys, including JSON escaped echoes.
                    safe = json.dumps(result, ensure_ascii=False)
                    for secret in sorted((self._api_key, self._access_code), key=len, reverse=True):
                        safe = safe.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[REDACTED]')
                    return status, safe, truncated
        except requests.RequestException:
            raise MungosError(
                'UNKNOWN_REMOTE_STATE | HTTP status: N/A | '
                f'Timeout ili mrežna greška; nema retryja. Provjerite Mungos prije novog {method.upper()}-a.'
            ) from None

    def liveness(self):
        """One GET, no redirects/retries/body consumption; return HTTP status only."""
        try:
            headers = {'X-Api-Key': self._api_key}
            if self._access_code:
                headers['ecommerceaccesscode'] = self._access_code
            with requests.Session() as session:
                with session.get(
                    self._url,
                    headers=headers,
                    timeout=self.TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                ) as response:
                    return response.status_code
        except requests.Timeout:
            raise MungosError('Istekao je timeout konekcije ili odgovora.') from None
        except requests.RequestException:
            # Never print exception/request/response: they may contain credentials.
            raise MungosError('Konekcija prema Mungosu nije uspjela (mreža/TLS/HTTP).') from None
