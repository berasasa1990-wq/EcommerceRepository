from django import forms
from .services import validate_control_password


class PasswordForm(forms.Form):
    password = forms.CharField(label='Nova lozinka', max_length=256, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), validators=[validate_control_password])
    confirm = forms.CharField(label='Potvrdi lozinku', max_length=256, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def clean(self):
        data = super().clean()
        if data.get('password') != data.get('confirm'):
            self.add_error('confirm', 'Lozinke se ne podudaraju.')
        return data


class BootstrapForm(PasswordForm):
    token = forms.CharField(label='Jednokratni bootstrap token', max_length=128, widget=forms.PasswordInput(attrs={'autocomplete': 'off'}))


class LoginForm(forms.Form):
    password = forms.CharField(label='Lozinka', max_length=256, widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))


class ActivationDurationForm(forms.Form):
    duration_days = forms.IntegerField(label='Trajanje aktivacije (dana)', required=False, min_value=1, max_value=36500)
