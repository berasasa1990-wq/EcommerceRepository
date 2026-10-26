import hashlib
import secrets
from datetime import timedelta
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from .models import ControlState, AuditEvent, ModuleSwitch

IDLE_SECONDS = 1800


def validate_control_password(password):
    validate_password(password)
    if len(password) < 12:
        raise ValidationError('Lozinka mora sadržavati najmanje 12 znakova.')


def local_allows(code):
    return not ModuleSwitch.objects.filter(code=code, enabled=False).exists()


def issue_bootstrap():
    token = secrets.token_urlsafe(32)
    changed = ControlState.objects.filter(pk=1, initialized_at__isnull=True).update(
        bootstrap_digest=hashlib.sha256(token.encode()).hexdigest(),
        bootstrap_expires=timezone.now() + timedelta(minutes=10))
    if not changed:
        raise PermissionDenied('Panel je već inicijalizovan.')
    AuditEvent.objects.create(action='bootstrap_issued')
    return token


@sensitive_variables('token', 'password')
def initialize(token, password, address=None):
    validate_control_password(password)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with transaction.atomic():
        changed = ControlState.objects.filter(pk=1, initialized_at__isnull=True,
            bootstrap_digest=digest, bootstrap_expires__gt=timezone.now()).update(
                password_hash=make_password(password), initialized_at=timezone.now(),
                bootstrap_digest='', bootstrap_expires=None, generation=F('generation') + 1)
        if not changed:
            raise PermissionDenied('Token nije važeći ili je panel već inicijalizovan.')
        AuditEvent.objects.create(action='initialized', address=address)


@sensitive_variables('password')
def recover(password):
    validate_control_password(password)
    with transaction.atomic():
        changed = ControlState.objects.filter(pk=1, initialized_at__isnull=False).update(
            password_hash=make_password(password), generation=F('generation') + 1,
            failures=0, locked_until=None, bootstrap_digest='', bootstrap_expires=None)
        if not changed:
            raise PermissionDenied('Panel još nije inicijalizovan.')
        AuditEvent.objects.create(action='password_recovered')


def activation_cipher():
    import base64
    from cryptography.fernet import Fernet
    from django.conf import settings
    # Reuse the server secret; no extra installation keys or user configuration.
    key = hashlib.sha256(('bera-local-activation-v1:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def display_activation_code(activation):
    from cryptography.fernet import InvalidToken
    try:
        return activation_cipher().decrypt(activation.encrypted_code.encode()).decode()
    except InvalidToken:
        return ''


def issue_module_code(code, duration_days=None):
    from bera_licensing.registry import IMPLEMENTED
    from .models import ModuleActivation
    if code not in IMPLEMENTED:
        raise PermissionDenied('Modul nije povezan.')
    raw = 'BERA-' + '-'.join(secrets.token_hex(4).upper() for _ in range(4))
    activation, _ = ModuleActivation.objects.update_or_create(module=code, defaults={
        'code_digest': hashlib.sha256(raw.encode()).hexdigest(),
        'encrypted_code': activation_cipher().encrypt(raw.encode()).decode(),
        'activated_at': None, 'duration_days': duration_days, 'expires_at': None,
    })
    return activation


@sensitive_variables('raw')
def redeem_module_code(code, raw, address=None):
    from bera_licensing.registry import IMPLEMENTED
    from .models import ModuleActivation
    if code not in IMPLEMENTED or not isinstance(raw, str) or len(raw) > 128:
        return False
    digest = hashlib.sha256(raw.strip().upper().encode()).hexdigest()
    with transaction.atomic():
        # Same serialization point as the owner's switches; no partial activation.
        ControlState.objects.filter(pk=1).update(failures=F('failures'))
        if not local_allows(code):
            return False
        now = timezone.now()
        activation = ModuleActivation.objects.filter(module=code, code_digest=digest, activated_at__isnull=True).first()
        if activation is None:
            return False
        expires = now + timedelta(days=activation.duration_days) if activation.duration_days else None
        changed = ModuleActivation.objects.filter(module=code, code_digest=digest, activated_at__isnull=True).update(activated_at=now, expires_at=expires)
        if changed:
            AuditEvent.objects.create(action='module_activated', module=code, previous=False, current=True, address=address)
        return bool(changed)


def expiry_status(expires_at, now=None):
    import math
    now = now or timezone.now()
    remaining = expires_at - now if expires_at else None
    return {
        'expires_at': expires_at,
        'expired': bool(remaining is not None and remaining.total_seconds() <= 0),
        'expiry_days': math.ceil(remaining.total_seconds() / 86400) if remaining is not None and timedelta(0) < remaining <= timedelta(days=7) else None,
    }
