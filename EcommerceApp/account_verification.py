"""Email confirmation tokens are distinct from password-reset tokens."""
from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = 'EcommerceApp.account_verification.EmailVerificationTokenGenerator'

    def _make_hash_value(self, user, timestamp):
        return super()._make_hash_value(user, timestamp) + str(user.is_active)


verification_token_generator = EmailVerificationTokenGenerator()


class VerificationEmailError(Exception):
    pass


def send_verification_email(request, user):
    path = reverse('activate', kwargs={
        'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': verification_token_generator.make_token(user),
    })
    link = request.build_absolute_uri(path)
    try:
        sent = send_mail(
            'Potvrdite email adresu — Carpologija BH',
            'Dobrodošli na Carpologija BH!\n\n'
            'Za aktivaciju naloga i prijavu kliknite na ovaj link:\n'
            f'{link}\n\nAko niste zatražili registraciju, zanemarite ovu poruku.',
            settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False,
        )
        if sent != 1:
            raise VerificationEmailError()
    except Exception:
        # SMTP exceptions can contain credentials or tokens; never expose them.
        raise VerificationEmailError('Verifikacioni email nije poslan.') from None
