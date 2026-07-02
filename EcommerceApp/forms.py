from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import User

from .models import Banner, Brand, Category, Popup, Product, Tag


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
        label='Kupon kod',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Unesite loyalty kod',
            'autocomplete': 'off',
        }),
    )

    def clean_kod(self):
        kod = self.cleaned_data.get('kod', '').strip()
        if not kod:
            raise forms.ValidationError('Unesite kupon kod.')
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
