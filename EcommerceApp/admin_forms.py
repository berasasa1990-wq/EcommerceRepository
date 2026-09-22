"""Turnstile protection scoped to the standard Django admin login."""
from django import forms
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm


class AdminTurnstilePasswordInput(forms.PasswordInput):
    # The standard admin login renders the password widget inside its CSRF form.
    # Keep Django's template and fields rather than copying the entire login page.
    template_name = 'admin/widgets/turnstile_password.html'

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['turnstile_site_key'] = getattr(settings, 'TURNSTILE_SITE_KEY', '')
        return context


class TurnstileAdminAuthenticationForm(AdminAuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        original = self.fields['password'].widget
        self.fields['password'].widget = AdminTurnstilePasswordInput(attrs=original.attrs)

    def clean(self):
        # Turnstile keys are deliberately disabled in local DEBUG mode.
        # Keep normal Django admin authentication available for local work.
        if settings.DEBUG:
            return super().clean()
        # Never allow missing configuration or a failed challenge to bypass login
        # in production.
        if not (getattr(settings, 'TURNSTILE_SITE_KEY', '')
                and getattr(settings, 'TURNSTILE_SECRET_KEY', '')):
            raise forms.ValidationError(
                'Sigurnosna provjera prijave nije podešena. Obratite se administratoru servera.',
                code='turnstile_configuration',
            )
        token = self.data.get('cf_turnstile_response', '').strip()
        if not token or len(token) > 2048:
            raise forms.ValidationError(
                'Molimo potvrdite da niste robot (Turnstile) i pokušajte ponovo.',
                code='turnstile_required',
            )
        # Reuse registration's existing verifier without changing its behavior.
        from .views import verify_turnstile
        if verify_turnstile(token, self.request) is not True:
            raise forms.ValidationError(
                'Turnstile provjera nije uspjela ili je istekla. Molimo pokušajte ponovo.',
                code='turnstile_failed',
            )
        return super().clean()
