import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


@login_required(login_url='login')
@require_POST
def request_unlock(request):
    user = request.user
    if not user.is_active or not (user.is_staff or user.is_superuser):
        return JsonResponse({'message': 'Nemate pristup panelu.'}, status=403)
    option = (request.POST.get('option') or '').strip()
    if not option or len(option) > 150:
        return JsonResponse({'message': 'Izaberite opciju za otključavanje.'}, status=400)
    key = f'module-unlock-request:{user.pk}'
    if not cache.add(key, True, timeout=60):
        return JsonResponse({'message': 'Zahtjev je već poslan. Sačekajte minut prije novog zahtjeva.'}, status=429)
    profile = getattr(user, 'profil', None)
    data = {
        'option': option, 'domain': request.get_host(),
        'site_url': request.build_absolute_uri('/'), 'sent_at': timezone.localtime(),
        'name': user.get_full_name() or user.get_username(),
        'username': user.get_username(), 'email': user.email,
        'user_id': user.pk, 'joined': user.date_joined, 'last_login': user.last_login,
        'role': 'Administrator' if user.is_superuser else 'Osoblje',
        'phone': getattr(profile, 'telefon', ''), 'address': getattr(profile, 'adresa', ''),
        'city': getattr(profile, 'grad', ''), 'postal': getattr(profile, 'postanski_broj', ''),
    }
    html = render_to_string('bera_reference/emails/module_unlock_request.html', data)
    message = EmailMultiAlternatives(
        subject=f'Zahtjev za otključavanje opcije — {request.get_host()}',
        body=strip_tags(html), from_email=settings.DEFAULT_FROM_EMAIL,
        to=['sasabera1990@proton.me'], reply_to=[user.email] if user.email else [],
    )
    message.attach_alternative(html, 'text/html')
    try:
        sent = message.send(fail_silently=False)
        if not sent:
            raise RuntimeError('Email backend did not accept the message')
    except Exception:
        cache.delete(key)
        logger.exception('Module unlock email failed for user %s', user.pk)
        return JsonResponse({'message': 'Slanje nije uspjelo. Pokušajte ponovo.'}, status=503)
    return JsonResponse({'message': 'Zahtjev je poslan. Kontaktirat ćemo vas radi otključavanja opcije.'})
