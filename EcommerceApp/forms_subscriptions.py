from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import MagacinPlan
from .warehouse_access import FEATURES, is_subscription_owner


class PlanFeaturesForm(forms.Form):
    features = forms.MultipleChoiceField(label='Dozvoljene sekcije', choices=list(FEATURES.items()), widget=forms.CheckboxSelectMultiple, required=False)


class SubscriptionForm(forms.Form):
    email = forms.EmailField(label='Email korisnika')
    plan = forms.ModelChoiceField(label='Plan', queryset=MagacinPlan.objects.all().order_by('id'), empty_label=None)
    active = forms.BooleanField(label='Aktivna pretplata', required=False, initial=True)
    expires_on = forms.DateField(label='Vrijedi do (prazno = bez roka)', required=False, widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        users = list(get_user_model().objects.filter(email__iexact=email, is_active=True)[:2])
        if len(users) != 1:
            raise forms.ValidationError('Potreban je tačno jedan aktivan korisnik s ovim emailom.')
        self.target_user = users[0]
        if is_subscription_owner(self.target_user):
            raise forms.ValidationError('Vlasnik uvijek ima puni pristup.')
        return email

    def clean(self):
        cleaned = super().clean()
        expires = cleaned.get('expires_on')
        if cleaned.get('active') and expires and expires < timezone.localdate():
            self.add_error('expires_on', 'Datum isteka ne može biti u prošlosti za aktivnu pretplatu.')
        return cleaned
