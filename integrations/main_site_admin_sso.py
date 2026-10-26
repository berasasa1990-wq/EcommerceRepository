"""Copy this view into the main Django site; configure the key in server settings."""
import secrets
from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.core import signing
from django.http import HttpResponse
from django.utils.html import format_html
from django.views.decorators.http import require_POST


@user_passes_test(lambda user: user.is_active and user.is_superuser)
@require_POST
def open_shop_admin(request):
    token = signing.dumps(
        {'aud': 'bera-shop-admin', 'nonce': secrets.token_urlsafe(32)},
        key=settings.SHOP_ADMIN_SSO_KEY, salt='bera-admin-sso-v1',
    )
    response = HttpResponse(format_html(
        '<!doctype html><html lang="bs"><meta charset="utf-8">'
        '<meta name="referrer" content="no-referrer"><title>Otvori admin</title>'
        '<form method="post" action="{}"><input type="hidden" name="token" value="{}">'
        '<button type="submit">Otvori Django admin</button></form>'
        '<script>document.forms[0].submit();</script></html>',
        settings.SHOP_ADMIN_SSO_URL, token,
    ))
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
