import hashlib
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from .models import AdminLoginNonce

SALT = 'bera-admin-sso-v1'


@sensitive_post_parameters('token')
@csrf_exempt
@require_POST
def admin_sso_login(request):
    # The signed one-use token authenticates this cross-site POST.
    key = settings.ADMIN_SSO_KEY
    token = request.POST.get('token', '')
    if not key or not token or len(token) > 2048:
        return HttpResponseForbidden('Pristup nije odobren.')
    try:
        payload = signing.loads(token, key=key, salt=SALT, max_age=60, fallback_keys=[])
    except (signing.BadSignature, ValueError, TypeError):
        return HttpResponseForbidden('Token nije ispravan ili je istekao.')
    if not isinstance(payload, dict) or payload.get('aud') != 'bera-shop-admin' or not isinstance(payload.get('nonce'), str) or not 32 <= len(payload['nonce']) <= 128:
        return HttpResponseForbidden('Pristup nije odobren.')
    user = get_user_model().objects.filter(username=settings.ADMIN_SSO_USERNAME, is_active=True, is_superuser=True).first()
    if user is None:
        return HttpResponseForbidden('Administrator nije konfigurisan.')
    now = timezone.now()
    digest = hashlib.sha256(payload['nonce'].encode()).hexdigest()
    try:
        with transaction.atomic():
            AdminLoginNonce.objects.filter(expires_at__lt=now).delete()
            AdminLoginNonce.objects.create(digest=digest, expires_at=now + timedelta(seconds=120))
    except IntegrityError:
        return HttpResponseForbidden('Token je već iskorišten.')
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    response = redirect('admin:index')
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
