from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import User

import re

from django.core.exceptions import ValidationError as DjangoValidationError
from .models import (
    Akcija,
    Banner,
    Brand,
    Category,

    Popup,
    Product,
    Tag,
)


class AkcijaAdminForm(forms.ModelForm):
    class Meta:
        model = Akcija
        fields = '__all__'

    def clean_artikal(self):
        artikal = self.cleaned_data.get('artikal')
        tip = self.cleaned_data.get('tip') or getattr(self.instance, 'tip', None)
        tip = self.cleaned_data.get('tip') or getattr(self.instance, 'tip', None)
        if tip == Akcija.Tip.BUNDLE:
            # artikal je opcionalan — samo za trigger „odabrani trigger artikal”
            trigger = self.cleaned_data.get('bundle_trigger') or getattr(
                self.instance, 'bundle_trigger', None,
            )
            if trigger == Akcija.BundleTrigger.TRIGGER_PRODUCT and not artikal:
                raise forms.ValidationError('Odaberite trigger artikal.')
            if artikal and not artikal.aktivan:
                raise forms.ValidationError('Artikal mora biti aktivan na sajtu.')
            return artikal
        if tip in {
            Akcija.Tip.TIMER,
            Akcija.Tip.USLOV,
            Akcija.Tip.X_PLUS_1,
            Akcija.Tip.KORPA_NUDJENJE,
            Akcija.Tip.GRATIS,
        } and not artikal:
            raise forms.ValidationError('Odaberite artikal.')
        if artikal and not artikal.aktivan:
            raise forms.ValidationError('Artikal mora biti aktivan na sajtu.')
        return artikal

    def clean_gratis_artikal(self):
        gratis_artikal = self.cleaned_data.get('gratis_artikal')
        tip = self.cleaned_data.get('tip') or getattr(self.instance, 'tip', None)
        if tip != Akcija.Tip.GRATIS:
            return gratis_artikal
        if not gratis_artikal:
            raise forms.ValidationError('Odaberite gratis artikal.')
        if not gratis_artikal.aktivan:
            raise forms.ValidationError('Artikal mora biti aktivan na sajtu.')
        return gratis_artikal

    def clean(self):
        cleaned = super().clean()
        tip = cleaned.get('tip') or getattr(self.instance, 'tip', None)
        if not tip:
            return cleaned

        if tip == Akcija.Tip.SLIKA:
            has_slika = bool(cleaned.get('slika')) or bool(getattr(self.instance, 'slika', None))
            if not has_slika:
                self.add_error('slika', 'Obavezna slika za pop-up.')

        elif tip == Akcija.Tip.TIMER:
            for field, label in (
                ('pocetak', 'Početak akcije'),
                ('trajanje_sati', 'Trajanje akcije'),
                ('popust_postotak', 'Popust (%)'),
            ):
                if cleaned.get(field) in (None, ''):
                    self.add_error(field, f'Obavezno ({label}).')

        elif tip == Akcija.Tip.USLOV:
            for field, label in (
                ('pocetak', 'Početak akcije'),
                ('trajanje_sati', 'Trajanje akcije'),
                ('popust_postotak', 'Popust (%)'),
                ('prag_korpe_km', 'Uslov iznosa u korpi'),
            ):
                if cleaned.get(field) in (None, ''):
                    self.add_error(field, f'Obavezno ({label}).')

        elif tip == Akcija.Tip.X_PLUS_1:
            if not cleaned.get('deal_vrsta'):
                self.add_error('deal_vrsta', 'Odaberite vrstu (1+1, 2+1 ili 3+1).')
            if cleaned.get('popust_postotak') is None:
                self.add_error('popust_postotak', 'Unesite % popusta na dodatni artikal.')

        elif tip == Akcija.Tip.KORPA_NUDJENJE:
            for field, label in (
                ('popust_postotak', 'Popust (%)'),
                ('kategorija', 'Kategorija (trigger)'),
            ):
                if cleaned.get(field) in (None, ''):
                    self.add_error(field, f'Obavezno ({label}).')

        elif tip == Akcija.Tip.GRATIS:
            artikal = cleaned.get('artikal')
            gratis_artikal = cleaned.get('gratis_artikal')
            if cleaned.get('popust_postotak') in (None, ''):
                self.add_error('popust_postotak', 'Unesite % popusta na drugi artikal.')
            if artikal and gratis_artikal and artikal.pk == gratis_artikal.pk:
                self.add_error('gratis_artikal', 'Gratis artikal mora biti različit od trigger artikla.')

        elif tip == Akcija.Tip.BUNDLE:
            if cleaned.get('popust_postotak') in (None, ''):
                self.add_error('popust_postotak', 'Unesite % popusta na kompletan set.')
            trigger = cleaned.get('bundle_trigger') or Akcija.BundleTrigger.DELAY
            if trigger == Akcija.BundleTrigger.TRIGGER_PRODUCT and not cleaned.get('artikal'):
                self.add_error('artikal', 'Odaberite trigger artikal.')
            if trigger == Akcija.BundleTrigger.CATEGORY and not cleaned.get('kategorija'):
                self.add_error('kategorija', 'Odaberite trigger kategoriju.')
            # Validacija linija (qty) ili legacy M2M — u ModelAdmin.save_related

        return cleaned


class PopupAdminForm(forms.ModelForm):
    class Meta:
        model = Popup
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        tip = cleaned_data.get('tip')
        if tip == Popup.Tip.AKCIJA:
            for field, label in (
                ('akcija_sati', 'Trajanje akcije (sati)'),
                ('akcija_pocetak', 'Početak akcije'),
                ('akcija_artikal', 'Artikal u akciji'),
            ):
                if not cleaned_data.get(field):
                    self.add_error(field, f'Obavezno za akcijski pop-up ({label}).')
            # New conditional discount fields are optional but recommended together
            popust = cleaned_data.get('akcija_popust_postotak')
            prag = cleaned_data.get('akcija_prag_iznos')
            if (popust is not None and prag is None) or (popust is None and prag is not None):
                self.add_error(None, 'Za uslovni popust morate unijeti i % popusta i prag iznosa.')
        elif tip == Popup.Tip.SLIKA:
            has_slika = bool(cleaned_data.get('slika')) or bool(getattr(self.instance, 'slika', None))
            if not has_slika:
                self.add_error('slika', 'Obavezno za pop-up sa slikom.')
        return cleaned_data


class BannerAdminForm(forms.ModelForm):
    class Meta:
        model = Banner
        fields = '__all__'

    def clean_video(self):
        video = self.cleaned_data.get('video')
        if not video:
            return video
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .utils.videos import validate_banner_video
        try:
            validate_banner_video(video)
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return video

    def clean(self):
        cleaned_data = super().clean()
        slika = cleaned_data.get('slika')
        video = cleaned_data.get('video')
        has_slika = bool(slika) or bool(getattr(self.instance, 'slika', None))
        has_video = bool(video) or bool(getattr(self.instance, 'video', None))
        if not has_slika and not has_video:
            raise forms.ValidationError('Banner mora imati sliku ili video.')

        cijena_od = cleaned_data.get('filter_cijena_od')
        cijena_do = cleaned_data.get('filter_cijena_do')
        if cijena_od is not None and cijena_do is not None and cijena_od > cijena_do:
            raise forms.ValidationError('Min. cijena ne može biti veća od maks. cijene.')
        has_destination = bool((cleaned_data.get('link') or '').strip()) or bool(cleaned_data.get('kategorija'))
        if (cijena_od is not None or cijena_do is not None) and not has_destination:
            raise forms.ValidationError('Za filter cijene odaberite kategoriju ili unesite link.')
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            try:
                instance.save()
            except ValueError as exc:
                raise forms.ValidationError({'slika': str(exc)}) from exc
            except Exception as exc:
                raise forms.ValidationError({'video': str(exc)}) from exc
        return instance


class RegisterForm(forms.Form):
    ime_prezime = forms.CharField(
        label='Ime i prezime',
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ime i prezime'}),
    )
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'email@primjer.ba'}),
    )
    telefon = forms.CharField(
        label='Telefon',
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '+387 6x xxx xxx'}),
    )
    lozinka = forms.CharField(
        label='Lozinka',
        min_length=8,
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Min. 8 znakova'}),
    )
    lozinka_potvrda = forms.CharField(
        label='Potvrdite lozinku',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Ponovite lozinku'}),
    )
    cf_turnstile_response = forms.CharField(
        required=True,
        widget=forms.HiddenInput(),
        error_messages={
            'required': 'Molimo potvrdite da niste robot (Turnstile).'
        }
    )

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
            raise forms.ValidationError('Korisnik s ovim emailom već postoji.')
        return email

    def clean(self):
        cleaned = super().clean()
        lozinka = cleaned.get('lozinka')
        potvrda = cleaned.get('lozinka_potvrda')
        if lozinka and potvrda and lozinka != potvrda:
            self.add_error('lozinka_potvrda', 'Lozinke se ne podudaraju.')
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'email@primjer.ba', 'autofocus': True}),
    )
    lozinka = forms.CharField(
        label='Lozinka',
        widget=forms.PasswordInput(attrs={'class': 'form-input', 'placeholder': 'Lozinka'}),
    )
    cf_turnstile_response = forms.CharField(
        required=True,
        widget=forms.HiddenInput(),
        error_messages={
            'required': 'Molimo potvrdite da niste robot (Turnstile).'
        }
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get('email', '').strip().lower()
        lozinka = cleaned.get('lozinka')
        if not email or not lozinka:
            return cleaned

        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.filter(username__iexact=email).first()

        if user is None:
            raise forms.ValidationError('Pogrešan email ili lozinka.')

        authenticated = authenticate(self.request, username=user.username, password=lozinka)
        if authenticated is None:
            raise forms.ValidationError('Pogrešan email ili lozinka.')
        if not authenticated.is_active:
            raise forms.ValidationError('Ovaj nalog je deaktiviran.')

        self.user = authenticated
        return cleaned


class LoyaltyIssueForm(forms.Form):
    ime = forms.CharField(
        label='Ime',
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'npr. Amira',
            'autocomplete': 'given-name',
        }),
    )
    prezime = forms.CharField(
        label='Prezime',
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'npr. Hadžić',
            'autocomplete': 'family-name',
        }),
    )
    telefon = forms.CharField(
        label='Telefon (Viber)',
        max_length=30,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'npr. 061 123 456',
            'autocomplete': 'tel',
            'inputmode': 'tel',
        }),
    )
    email = forms.EmailField(
        label='Email',
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'opcionalno — npr. amira@email.com',
            'autocomplete': 'email',
        }),
    )

    def clean_telefon(self):
        from .loyalty import telefon_vec_registrovan

        telefon = self.cleaned_data.get('telefon', '').strip()
        if not telefon:
            raise forms.ValidationError('Telefon je obavezan.')
        digits = ''.join(ch for ch in telefon if ch.isdigit())
        if len(digits) < 8:
            raise forms.ValidationError('Unesite ispravan broj telefona.')
        if telefon_vec_registrovan(telefon):
            raise forms.ValidationError('Ovaj broj telefona je već registrovan.')
        return telefon

    def clean_email(self):
        from .loyalty import email_vec_registrovan

        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        if email_vec_registrovan(email):
            raise forms.ValidationError('Ovaj email je već registrovan.')
        return email


class ProfileForm(forms.Form):
    ime_prezime = forms.CharField(
        label='Ime i prezime',
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'class': 'form-input'}),
    )
    telefon = forms.CharField(
        label='Telefon',
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )
    adresa = forms.CharField(
        label='Adresa',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )
    grad = forms.CharField(
        label='Grad',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )
    postanski_broj = forms.CharField(
        label='Poštanski broj',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input'}),
    )


class CouponForm(forms.Form):
    kod = forms.CharField(
        label='Broj kartice',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Unesite broj loyalty kartice',
            'autocomplete': 'off',
            'inputmode': 'text',
            'autocapitalize': 'characters',
        }),
    )

    def clean_kod(self):
        kod = self.cleaned_data.get('kod', '').strip().upper()
        if not kod:
            raise forms.ValidationError('Unesite broj kartice.')
        return kod.upper()


class CheckoutForm(forms.Form):
    ime_prezime = forms.CharField(
        label='Ime i prezime', max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ime i prezime'}),
    )
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'email@primjer.ba'}),
    )
    telefon = forms.CharField(
        label='Telefon', max_length=30,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '+387 6x xxx xxx'}),
    )
    adresa = forms.CharField(
        label='Adresa',
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ulica i broj'}),
    )
    grad = forms.CharField(
        label='Grad', max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Grad'}),
    )
    postanski_broj = forms.CharField(
        label='Poštanski broj', max_length=20, required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '71000'}),
    )
    napomena = forms.CharField(
        label='Napomena', required=False,
        widget=forms.Textarea(attrs={'class': 'form-input form-textarea', 'rows': 3, 'placeholder': 'Opcionalno'}),
    )


class OdooImportForm(forms.Form):
    odoo_category_id = forms.ChoiceField(
        label='Odoo product category',
        choices=[],
        widget=forms.Select(attrs={'class': 'odoo-select'}),
    )
    kategorija = forms.ModelChoiceField(
        label='Lokalna kategorija (opcionalno)',
        queryset=Category.objects.filter(aktivan=True).order_by('redoslijed', 'naziv'),
        required=False,
        empty_label='— automatski po Odoo mapiranju —',
        widget=forms.Select(attrs={'class': 'odoo-select'}),
        help_text='Ako je prazno, koristi lokalnu kategoriju s istim Odoo category ID.',
    )
    ukljuci_podkategorije = forms.BooleanField(
        label='Uključi podkategorije iz Odoo-a',
        required=False,
        initial=True,
    )
    azuriraj_postojece = forms.BooleanField(
        label='Ažuriraj postojeće artikle (po Odoo ID)',
        required=False,
        initial=True,
    )
    ucitaj_slike = forms.BooleanField(
        label='Učitaj slike iz Odoo-a',
        required=False,
        initial=True,
    )
    samo_stanje = forms.BooleanField(
        label='Samo ažuriraj stanje (postojeći artikli)',
        required=False,
        initial=False,
        help_text='Ažurira samo količinu i dostupnost. Ne mijenja naziv, cijenu, kategoriju, slike niti kreira nove artikle.',
    )
    samo_slike = forms.BooleanField(
        label='Samo učitaj/ažuriraj slike (postojeći artikli)',
        required=False,
        initial=False,
        help_text='Koristi nakon importa bez slika — povlači samo slike iz Odoo-a za artikle koji već postoje u bazi.',
    )
    preskoci_brendovi = forms.ModelMultipleChoiceField(
        label='Ne ažuriraj artikle ovih brendova',
        queryset=Brand.objects.order_by('naziv'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Označeni brendovi se preskaču pri ažuriranju postojećih artikala (korisno za brendove koji nisu u Odoo-u).',
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('samo_stanje'):
            cleaned['azuriraj_postojece'] = True
            cleaned['ucitaj_slike'] = False
            cleaned['samo_slike'] = False
        if cleaned.get('samo_slike'):
            cleaned['azuriraj_postojece'] = True
            cleaned['ucitaj_slike'] = True
            cleaned['samo_stanje'] = False
        return cleaned

    def __init__(self, *args, odoo_category_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        if odoo_category_choices is not None:
            self.fields['odoo_category_id'].choices = odoo_category_choices


class MergeProductsForm(forms.Form):
    glavni_artikal = forms.ModelChoiceField(
        label='Glavni artikal (zadržava sliku)',
        queryset=Product.objects.none(),
        widget=forms.RadioSelect,
    )
    naziv = forms.CharField(
        label='Naziv spojenog artikla (opcionalno)',
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'odoo-select', 'placeholder': 'Ostavite prazno za naziv glavnog artikla'}),
    )

    def __init__(self, *args, selected_products=None, **kwargs):
        super().__init__(*args, **kwargs)
        if selected_products is not None:
            self.fields['glavni_artikal'].queryset = selected_products
            self.fields['glavni_artikal'].initial = selected_products.first()


class BulkAssignCategoryForm(forms.Form):
    kategorija = forms.ModelChoiceField(
        label='Kategorija na sajtu',
        queryset=Category.objects.filter(aktivan=True).select_related(
            'roditelj', 'roditelj__roditelj',
        ).order_by('redoslijed', 'naziv'),
        widget=forms.Select(attrs={'class': 'odoo-select'}),
        empty_label=None,
    )


class BulkAssignBrandForm(forms.Form):
    brend = forms.ModelChoiceField(
        label='Brend',
        queryset=Brand.objects.order_by('naziv'),
        widget=forms.Select(attrs={'class': 'odoo-select'}),
        empty_label=None,
    )


class BulkAssignTagsForm(forms.Form):
    tagovi = forms.ModelMultipleChoiceField(
        label='Tagovi',
        queryset=Tag.objects.order_by('naziv'),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        help_text='Odabrani tagovi će biti dodani postojećim tagovima artikala (ne zamjenjuju ih).',
    )



