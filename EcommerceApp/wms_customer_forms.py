"""Shared telephone validation for the WMS customer create/edit paths."""
from django import forms
from .models import WMSCustomer, WMSSettings


def phone_key(value):
    return ''.join(character for character in value if character.isdecimal())


def lock_customer_writes():
    # All WMS writers take this singleton lock inside their atomic transaction.
    # This also serializes attempts to create a previously unseen telephone.
    WMSSettings.objects.get_or_create(pk=1)
    WMSSettings.objects.select_for_update().get(pk=1)


def customer_with_phone(telephone, exclude_pk=None):
    key = phone_key(telephone)
    if not key:
        return None
    for customer in WMSCustomer.objects.exclude(pk=exclude_pk).only('id', 'telefon', 'is_deleted'):
        if phone_key(customer.telefon) == key:
            return customer
    return None


class WMSCustomerForm(forms.ModelForm):
    class Meta:
        model = WMSCustomer
        fields = ['ime_prezime', 'telefon', 'adresa', 'grad', 'postanski_broj']

    def clean_telefon(self):
        telephone = self.cleaned_data['telefon'].strip()
        if not phone_key(telephone):
            raise forms.ValidationError('Unesite ispravan broj telefona.')
        if customer_with_phone(telephone, exclude_pk=self.instance.pk) is not None:
            raise forms.ValidationError('Kupac sa ovim brojem telefona već postoji.')
        return telephone
