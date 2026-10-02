"""Manual Mungos calls; no persistence or automatic synchronization."""
import json
from urllib.parse import urlsplit

import requests
from django.conf import settings


class MungosError(Exception):
    """Only fixed, credential-free messages may be exposed to the operator."""


class MungosClient:
    LIVENESS_PATH = '/Liveness/check/hello'
    PRODUCT_PATH = '/standard/product'
    MAX_RESPONSE_BYTES = 65536
    TIMEOUT = (5, 10)  # Connect and socket read timeout, in seconds.

    def __init__(self):
        if not settings.MUNGOS_ENABLED:
            raise MungosError('Mungos je isključen: postavite MUNGOS_ENABLED=true.')
        base_url = settings.MUNGOS_BASE_URL.strip().rstrip('/')
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
        if not api_key or not api_key.strip():
            raise MungosError('MUNGOS_API_KEY nije postavljen.')
        if any(ord(char) < 32 or ord(char) > 126 for char in api_key) or api_key != api_key.strip():
            raise MungosError('MUNGOS_API_KEY ima neispravan format za HTTP header.')
        access_code = settings.MUNGOS_ECOMMERCE_ACCESS_CODE
        if any(ord(char) < 32 or ord(char) > 126 for char in access_code) or access_code != access_code.strip():
            raise MungosError('MUNGOS_ECOMMERCE_ACCESS_CODE ima neispravan format za HTTP header.')
        self._base_url = base_url
        self._url = base_url + self.LIVENESS_PATH
        self._api_key = api_key
        self._access_code = access_code

    def send_product(self, payload):
        """Exactly one staging POST. Never retry an ambiguous remote write."""
        if urlsplit(self._base_url).hostname != 'staging.mungos.ba':
            raise MungosError('Slanje je dozvoljeno samo na staging.mungos.ba.')
        if not self._access_code:
            raise MungosError('STAGING slanje zahtijeva MUNGOS_ECOMMERCE_ACCESS_CODE.')
        try:
            with requests.Session() as session:
                with session.post(
                    self._base_url + self.PRODUCT_PATH,
                    json=payload,
                    headers={'X-Api-Key': self._api_key,
                             'ecommerceaccesscode': self._access_code},
                    timeout=self.TIMEOUT, allow_redirects=False, stream=True,
                ) as response:
                    status = response.status_code
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
                            'Čitanje odgovora nije uspjelo; nema retryja. Provjerite Mungos prije novog POST-a.'
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
                'Timeout ili mrežna greška; nema retryja. Provjerite Mungos prije novog POST-a.'
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
