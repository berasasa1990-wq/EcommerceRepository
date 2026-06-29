import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .sync_handlers import parse_json_body, upsert_korisnik, upsert_narudzba


def _auth_ok(request):
    expected = getattr(settings, 'SYNC_API_KEY', '')
    if not expected:
        return False
    provided = request.headers.get('X-Sync-Key', '')
    return provided == expected


@csrf_exempt
@require_POST
def sync_korisnik_api(request):
    if not _auth_ok(request):
        return JsonResponse({'ok': False, 'error': 'Neautorizovan zahtjev.'}, status=401)

    payload = parse_json_body(request)
    if not payload or not payload.get('email'):
        return JsonResponse({'ok': False, 'error': 'Nedostaje email.'}, status=400)

    try:
        result = upsert_korisnik(payload)
        return JsonResponse(result)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)


@csrf_exempt
@require_POST
def sync_narudzba_api(request):
    if not _auth_ok(request):
        return JsonResponse({'ok': False, 'error': 'Neautorizovan zahtjev.'}, status=401)

    payload = parse_json_body(request)
    if not payload or not payload.get('broj'):
        return JsonResponse({'ok': False, 'error': 'Nedostaje broj narudžbe.'}, status=400)

    try:
        result = upsert_narudzba(payload)
        return JsonResponse(result)
    except Exception as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)