"""Manual Mungos calls; no persistence or automatic synchronization."""
import json
from uuid import UUID

import requests
from django.conf import settings

from .mungos_payload import sanitize_mungos_payload
from .mungos_partial import validate_partial_payload


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


ENDPOINTS = {
    'staging': 'https://staging.mungos.ba/api/v1/connector',
    'production': 'https://mungos.ba/api/v1/connector',
}


def configured_endpoint():
    environment = settings.MUNGOS_ENVIRONMENT
    expected = ENDPOINTS.get(environment)
    configured = settings.MUNGOS_BASE_URL
    if not expected or not isinstance(configured, str) or configured.rstrip('/') != expected:
        raise MungosError('NOT_SENT | Neispravan MUNGOS_ENVIRONMENT ili endpoint za izabrano okruženje.')
    return expected


class MungosClient:
    LIVENESS_PATH = '/Liveness/check/hello'
    PRODUCT_PATH = '/standard/product'
    MAX_RESPONSE_BYTES = 65536
    TIMEOUT = (5, 10)  # Connect and socket read timeout, in seconds.

    def __init__(self):
        if not settings.MUNGOS_ENABLED:
            raise MungosError('Mungos je isključen: postavite MUNGOS_ENABLED=true.')
        base_url = configured_endpoint()
        production = settings.MUNGOS_ENVIRONMENT == 'production'
        api_key = settings.MUNGOS_PRODUCTION_API_KEY if production else settings.MUNGOS_API_KEY
        if not isinstance(api_key, str) or not api_key.strip():
            raise MungosError('NOT_SENT | Mungos API key za izabrano okruženje nije postavljen.')
        if any(ord(char) < 32 or ord(char) > 126 for char in api_key) or api_key != api_key.strip():
            raise MungosError('MUNGOS_API_KEY ima neispravan format za HTTP header.')
        access_code = (settings.MUNGOS_PRODUCTION_ECOMMERCE_ACCESS_CODE if production
                       else settings.MUNGOS_ECOMMERCE_ACCESS_CODE)
        if (not isinstance(access_code, str)
                or any(ord(char) < 32 or ord(char) > 126 for char in access_code)
                or access_code != access_code.strip()):
            raise MungosError('MUNGOS_ECOMMERCE_ACCESS_CODE ima neispravan format za HTTP header.')
        if production and not access_code:
            raise MungosError('NOT_SENT | Production zahtijeva MUNGOS_PRODUCTION_ECOMMERCE_ACCESS_CODE.')
        self._base_url = base_url
        self._url = base_url + self.LIVENESS_PATH
        self._api_key = api_key
        self._access_code = access_code

    def send_product(self, payload):
        """Exactly one POST. Never retry an ambiguous remote write."""
        return self._write_product('post', self.PRODUCT_PATH, payload)

    def update_product(self, mungos_uuid, payload):
        """Exactly one full PUT to an existing UUID, with no create fallback."""
        mungos_uuid = validate_product_uuid(mungos_uuid)
        return self._write_product('put', self.PRODUCT_PATH + '/' + mungos_uuid, payload)

    def sync_price(self, mungos_uuid, payload):
        # Price updates must preserve the regular/selling pair on product PUT.
        return self.update_product(mungos_uuid, payload)

    def sync_quantity(self, mungos_uuid, payload):
        return self._write_partial(mungos_uuid, payload, 'quantity')

    def _write_partial(self, mungos_uuid, payload, operation):
        if operation != 'quantity':
            raise MungosError('NOT_SENT | Samo quantity koristi partial endpoint.')
        mungos_uuid = validate_product_uuid(mungos_uuid)
        return self._write_product('put', self.PRODUCT_PATH + '/' + mungos_uuid + '/' + operation,
                                   payload, partial_operation=operation)

    def _write_product(self, method, path, payload, partial_operation=None):
        if self._base_url != configured_endpoint():
            raise MungosError('NOT_SENT | Konfiguracija okruženja je promijenjena.')
        if not self._access_code:
            raise MungosError('Slanje zahtijeva access code za izabrano okruženje.')
        if partial_operation:
            reasons = [] if validate_partial_payload(payload, partial_operation) else ['invalid_partial_payload']
        else:
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
