import json
import ssl
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from .security import LicenseError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LicenseError('redirect_refused')


@sensitive_variables()
def post(endpoint, payload, credential=None):
    url = settings.BERA_LICENSE_SERVER_URL
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LicenseError('invalid_server_url')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if credential is not None:
        if not credential or any(c in credential for c in '\r\n'):
            raise LicenseError('missing_configuration')
        headers['Authorization'] = 'Bearer ' + credential
    request = Request(url.rstrip('/') + '/' + endpoint + '/', data=json.dumps(payload).encode(), method='POST', headers=headers)
    try:
        opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request, timeout=10) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise LicenseError('response_too_large')
            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            return json.loads(raw, object_pairs_hook=unique_pairs)
    except LicenseError:
        raise
    except HTTPError as exc:
        code = 'invalid_activation_code' if endpoint == 'activate' and exc.code in (400, 401, 403, 404, 409, 410, 422) else 'server_unavailable'
        exc.close()
        raise LicenseError(code) from None
    except Exception:
        raise LicenseError('server_unavailable') from None


@sensitive_variables()
def fetch_license():
    from .credentials import installation_id, api_credential
    installation = installation_id()
    if not installation:
        raise LicenseError('missing_configuration')
    return post('sync', {'installation_id': installation,
                       'platform_version': settings.BERA_PLATFORM_VERSION, 'protocol_version': 1}, api_credential())


@sensitive_variables()
def activate_remote(installation, activation_code):
    return post('activate', {'installation_id': installation, 'activation_code': activation_code,
                            'platform_version': settings.BERA_PLATFORM_VERSION, 'protocol_version': 1})
