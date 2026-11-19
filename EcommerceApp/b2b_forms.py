from django import forms
from django.contrib.auth.password_validation import validate_password

from .models import B2BAccount


class B2BAccountForm(forms.ModelForm):
    new_password = forms.CharField(
        label='Nova šifra', widget=forms.PasswordInput, required=False,
        help_text='Obavezna za novog korisnika. Ostavite prazno da zadržite postojeću šifru.',
    )

    class Meta:
        model = B2BAccount
        fields = [
            'username', 'company', 'ime_prezime', 'telefon', 'adresa', 'grad',
            'postanski_broj', 'email', 'vp_kupac', 'odbio_posiljku', 'is_active',
        ]

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        if not password and not self.instance.pk:
            raise forms.ValidationError('Unesite šifru za novog B2B korisnika.')
        if password:
            validate_password(password)
        return password

    def save(self, commit=True):
        account = super().save(commit=False)
        if self.cleaned_data.get('new_password'):
            account.set_password(self.cleaned_data['new_password'])
        if commit:
            account.save()
        return account
