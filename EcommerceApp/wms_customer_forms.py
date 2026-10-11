import re

from django import forms

from .models import WMSCustomer


def phone_identity(value):
    digits = re.sub(r'\D', '', value or '')
    if digits.startswith('00387'):
        digits = digits[2:]
    if digits.startswith('387'):
        digits = digits[3:]
    return digits.lstrip('0')


class WMSCustomerForm(forms.ModelForm):
    class Meta:
        model = WMSCustomer
        fields = ['ime_prezime', 'telefon', 'adresa', 'grad', 'postanski_broj']

    def clean_telefon(self):
        phone = self.cleaned_data['telefon'].strip()
        identity = phone_identity(phone)
        if not identity:
            raise forms.ValidationError('Unesite ispravan broj telefona.')
        customers = WMSCustomer.objects.filter(is_deleted=False)
        if self.instance.pk:
            customers = customers.exclude(pk=self.instance.pk)
        if any(phone_identity(existing) == identity for existing in customers.values_list('telefon', flat=True).iterator()):
            raise forms.ValidationError('Kupac s ovim brojem telefona već postoji. Izaberite postojećeg kupca.')
        return phone
