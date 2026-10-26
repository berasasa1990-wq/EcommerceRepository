import logging
import uuid
from datetime import timedelta
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone
from .client import fetch_license
from .credentials import installation_id, centrally_managed
from .models import LicenseState
from .registry import MODULES
from .security import LicenseError, verify_document

logger = logging.getLogger(__name__)


def sync_license(*, force=False):
    installation = installation_id()
    if not installation:
        raise LicenseError('missing_configuration')
    state, _ = LicenseState.objects.get_or_create(installation_id=installation)
    now = timezone.now()
    token = str(uuid.uuid4())
    eligible = LicenseState.objects.filter(pk=state.pk).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
    if not force:
        eligible = eligible.filter(Q(next_attempt__isnull=True) | Q(next_attempt__lte=now))
    if not eligible.update(lease_token=token, lease_until=now + timedelta(seconds=60), last_attempt=now):
        return False
    owned = LicenseState.objects.filter(pk=state.pk, lease_token=token)
    try:
        document = fetch_license()
        issued, _, _ = verify_document(document, accepting=True)
        state.refresh_from_db()
        if document['license_version'] < state.version:
            raise LicenseError('stale_license')
        if document['license_version'] == state.version and document != state.document:
            raise LicenseError('version_conflict')
        if state.document:
            try:
                previous_issued = verify_document(state.document)[0]
            except LicenseError:
                previous_issued = None  # Allow recovery from corrupt cache or a rotated public key.
            if previous_issued is not None and issued < previous_issued:
                raise LicenseError('stale_license')
        # Conditional version + lease update works across workers without process-local locks.
        changed = owned.filter(version=state.version).update(
            document=document, version=document['license_version'], last_success=timezone.now(),
            last_error='', next_attempt=now + timedelta(seconds=settings.BERA_LICENSE_REFRESH_INTERVAL),
            lease_token='', lease_until=None)
        return bool(changed)
    except LicenseError as exc:
        owned.update(last_error=str(exc), next_attempt=now + timedelta(seconds=settings.BERA_LICENSE_REFRESH_INTERVAL), lease_token='', lease_until=None)
        logger.warning('BERA license sync failed: %s', exc)
        raise


def license_details():
    try:
        state = LicenseState.objects.filter(installation_id=installation_id()).first()
        if state is None or not state.document:
            return None
        issued, expires, grace = verify_document(state.document)
        if state.version != state.document['license_version']:
            return None
        limit = min(grace, expires + timedelta(seconds=settings.BERA_LICENSE_GRACE_PERIOD))
        return state, expires, limit
    except (LicenseError, DatabaseError):
        return None


def module_enabled(code):
    from bera_control.services import local_allows
    if not local_allows(code):
        return False
    if code not in MODULES:
        return False
    from bera_control.models import ModuleActivation
    from .registry import IMPLEMENTED
    activation = ModuleActivation.objects.filter(module=code).first()
    if activation is not None:
        return bool(code in IMPLEMENTED and activation.activated_at is not None and (activation.expires_at is None or timezone.now() < activation.expires_at))
    from .registry import PAID_SALE_MODULES
    if code in PAID_SALE_MODULES:
        return False  # Paid promotions require an owner-issued, redeemed local code.
    if code not in settings.BERA_LICENSE_ENFORCED_MODULES and not centrally_managed():
        # Only the existing Loyalty behavior is preserved during staged rollout.
        if code == 'loyalty':
            from shop.modules import legacy_loyalty_enabled
            return legacy_loyalty_enabled()
        return False
    details = license_details()
    return bool(details and timezone.now() < details[2] and details[0].document['modules'].get(code) is True)


def require_module(code):
    """Use in business operations and jobs; no HTTP dependency."""
    if not module_enabled(code):
        raise PermissionDenied('Modul nije aktivan.')


from django.views.decorators.debug import sensitive_variables


@sensitive_variables()
def activate_license(installation, activation_code):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from .credentials import cipher, connection
    from .client import activate_remote
    from .models import LicenseConnection
    if not isinstance(installation, str) or not installation.strip() or len(installation) > 200 or not isinstance(activation_code, str) or not activation_code.strip() or len(activation_code) > 512:
        raise LicenseError('invalid_activation_input')
    installation = installation.strip()
    # Do not consume a one-time code unless local encryption and trust are ready.
    encryption = cipher()
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.BERA_LICENSE_PUBLIC_KEY, validate=True))
    except (ValueError, TypeError):
        raise LicenseError('invalid_public_key_configuration') from None
    if connection():
        raise LicenseError('already_activated')
    result = activate_remote(installation, activation_code.strip())
    credential = result.get('api_credential') if isinstance(result, dict) else None
    if not isinstance(credential, str) or not credential or len(credential) > 8192 or any(c in credential for c in '\r\n'):
        raise LicenseError('invalid_activation_response')
    # Only the credential is accepted; public keys/documents in this response are ignored.
    LicenseConnection.objects.create(pk=1, installation_id=installation,
        encrypted_credential=encryption.encrypt(credential.encode()).decode('ascii'))
    return sync_license(force=True)
