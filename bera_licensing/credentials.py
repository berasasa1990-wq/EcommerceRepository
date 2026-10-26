"""Installation secrets: never expose this model through admin or template context."""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from .models import LicenseConnection
from .security import LicenseError


def connection():
    return LicenseConnection.objects.filter(pk=1).first()


def installation_id():
    stored = connection()
    return stored.installation_id if stored else settings.BERA_LICENSE_INSTALLATION_ID


def centrally_managed():
    return connection() is not None


def cipher():
    try:
        return Fernet(settings.BERA_LICENSE_ENCRYPTION_KEY.encode('ascii'))
    except (ValueError, TypeError, UnicodeError):
        raise LicenseError('invalid_encryption_configuration') from None


@sensitive_variables()
def api_credential():
    stored = connection()
    if not stored:
        return settings.BERA_LICENSE_API_CREDENTIAL
    try:
        return cipher().decrypt(stored.encrypted_credential.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError, UnicodeError):
        raise LicenseError('credential_unavailable') from None
