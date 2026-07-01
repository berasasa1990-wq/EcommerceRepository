import secrets

from django.conf import settings
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme


def site_prep_unlock(request):
    if not getattr(settings, 'SITE_PREP_ENABLED', False):
        return redirect('home')

    session_key = getattr(settings, 'SITE_PREP_SESSION_KEY', 'site_prep_unlocked')
    next_url = request.GET.get('next') or request.POST.get('next') or '/'
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = '/'

    if request.session.get(session_key):
        return redirect(next_url)

    error = None
    if request.method == 'POST':
        entered = request.POST.get('lozinka', '')
        expected = getattr(settings, 'SITE_PREP_PASSWORD', '')
        if expected and secrets.compare_digest(entered, expected):
            request.session[session_key] = True
            request.session.modified = True
            return redirect(next_url)
        error = 'Pogrešna lozinka. Pokušajte ponovo.'

    return render(request, 'site_prep.html', {
        'error': error,
        'next_url': next_url,
    })