"""Manual, read-only Mungos connectivity probe. No catalog or order access."""
from urllib.parse import urlsplit

import requests
from django.conf import settings


class MungosError(Exception):
    """Only fixed, credential-free messages may be exposed to the operator."""


class MungosClient:
    LIVENESS_PATH = '/Liveness/check/message'
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
        self._url = base_url + self.LIVENESS_PATH
        self._api_key = api_key

    def liveness(self):
        """One GET, no redirects/retries/body consumption; return HTTP status only."""
        try:
            with requests.Session() as session:
                with session.get(
                    self._url,
                    headers={'x-api-key': self._api_key},
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
