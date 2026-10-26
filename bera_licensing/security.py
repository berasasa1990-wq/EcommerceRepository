import base64
import json
from datetime import timedelta
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime


class LicenseError(Exception):
    """Public, fixed error code; never include remote content or credentials."""


def canonical_payload(document):
    return json.dumps({k: v for k, v in document.items() if k != 'signature'},
                      sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('utf-8')


def verify_document(document, *, accepting=False, now=None):
    now = now or timezone.now()
    try:
        if not isinstance(document, dict):
            raise ValueError()
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.BERA_LICENSE_PUBLIC_KEY, validate=True))
        key.verify(base64.b64decode(document['signature'], validate=True), canonical_payload(document))
        from .credentials import installation_id
        installation = installation_id()
        if not installation or document['installation_id'] != installation:
            raise ValueError()
        if type(document['protocol_version']) is not int or document['protocol_version'] != 1:
            raise ValueError()
        if type(document['license_version']) is not int or not 0 < document['license_version'] < 2**63:
            raise ValueError()
        if not isinstance(document['modules'], dict) or any(not isinstance(k, str) or type(v) is not bool for k, v in document['modules'].items()):
            raise ValueError()
        times = [parse_datetime(document[k]) for k in ('issued_at', 'expires_at', 'grace_until')]
        if any(t is None or timezone.is_naive(t) for t in times):
            raise ValueError()
        issued, expires, grace = times
        if not issued < expires <= grace or issued > now + timedelta(seconds=60):
            raise ValueError()
        if accepting and expires <= now:
            raise ValueError()
        return issued, expires, grace
    except (InvalidSignature, ValueError, TypeError, KeyError, OverflowError):
        raise LicenseError('invalid_license') from None
