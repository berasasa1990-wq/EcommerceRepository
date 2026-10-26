from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from .models import SiteModule


@sensitive_post_parameters('token')
@csrf_exempt
@require_POST
def update_loyalty_module(request):
    from bera_licensing.credentials import centrally_managed
    if 'loyalty' in settings.BERA_LICENSE_ENFORCED_MODULES or centrally_managed():
        return JsonResponse({'error': 'Legacy module control disabled'}, status=410)
    key = settings.MODULE_CONTROL_KEY
    token = request.POST.get('token', '')
    if not key or not token or len(token) > 4096:
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    try:
        data = signing.loads(token, key=key, salt='bera-module-control-v1', max_age=60, fallback_keys=[])
    except (signing.BadSignature, ValueError, TypeError):
        return JsonResponse({'error': 'Invalid or expired token'}, status=403)
    if not isinstance(data, dict) or data.get('aud') != 'bera-module-control' or data.get('module') != 'loyalty' or type(data.get('enabled')) is not bool or type(data.get('revision')) is not int or not 0 < data['revision'] < 2**63:
        return JsonResponse({'error': 'Invalid payload'}, status=400)
    paid_until = None
    if data.get('paid_until') is not None:
        try:
            paid_until = parse_datetime(data['paid_until'])
        except (TypeError, ValueError):
            paid_until = None
        if paid_until is None or timezone.is_naive(paid_until):
            return JsonResponse({'error': 'paid_until requires an ISO datetime with timezone'}, status=400)
    with transaction.atomic():
        module, _ = SiteModule.objects.get_or_create(code='loyalty')
        # Conditional update prevents older payment events from overriding newer ones.
        changed = SiteModule.objects.filter(pk=module.pk, revision__lt=data['revision']).update(
            enabled=data['enabled'], paid_until=paid_until, revision=data['revision'], updated_at=timezone.now())
        module.refresh_from_db()
        if not changed and (module.revision != data['revision'] or module.enabled != data['enabled'] or module.paid_until != paid_until):
            return JsonResponse({'error': 'Stale revision'}, status=409)
    response = JsonResponse({'module': 'loyalty', 'enabled': module.enabled, 'paid_until': module.paid_until, 'revision': module.revision})
    response['Cache-Control'] = 'no-store'
    return response
