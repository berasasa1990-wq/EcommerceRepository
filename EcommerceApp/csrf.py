import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import requires_csrf_token

logger = logging.getLogger(__name__)


@never_cache
@requires_csrf_token
def csrf_failure(request, reason=''):
    # Do not log submitted credentials, cookies or tokens.
    category = ('origin' if reason.startswith('Origin checking failed') else
                'referer' if reason.startswith('Referer checking failed') else
                'cookie' if 'cookie' in reason.lower() else 'token')
    logger.warning('CSRF rejected: path=%s category=%s', request.path, category)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(
            {
                'ok': False,
                'error': 'Sigurnosna provjera nije uspjela. Osvježi stranicu i pokušaj ponovo.',
            },
            status=403,
        )
    return render(request, 'auth/csrf_failure.html', {'retry_url': request.path}, status=403)
