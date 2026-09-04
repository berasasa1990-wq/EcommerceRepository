from django.http import JsonResponse
from django.views.csrf import csrf_failure as django_csrf_failure


def csrf_failure(request, reason=''):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(
            {
                'ok': False,
                'error': 'Sesija je istekla. Osvježi stranicu i pokušaj ponovo.',
            },
            status=403,
        )
    return django_csrf_failure(request, reason)
