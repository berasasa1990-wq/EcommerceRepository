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

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            raise forms.ValidationError(
                'Django administracija dostupna je samo superuser korisnicima.',
                code='superuser_required',
            )

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


class Multiple360Input(forms.ClearableFileInput):
    allow_multiple_selected = True


class Multiple360Field(forms.FileField):
    def clean(self, data, initial=None):
        if not data:
            return []
        return [super(Multiple360Field, self).clean(value, initial) for value in (data if isinstance(data, (list, tuple)) else [data])]


class ProductTagsAdminForm(forms.ModelForm):
    """Edit product tags as names separated by commas."""
    upload_360_zip = forms.FileField(label='Upload ZIP fotografija', required=False, widget=forms.FileInput(attrs={'accept': '.zip'}), help_text='2–120 kadrova istih dimenzija. ZIP do 100 MB; JPG, PNG i WebP.')
    upload_360_images = Multiple360Field(label='360° fotografije', required=False, widget=Multiple360Input(attrs={'accept': 'image/jpeg,image/png,image/webp'}))
    delete_360 = forms.BooleanField(label='Obriši 360° fotografije', required=False)
    wms_raspored = forms.CharField(required=False, widget=forms.HiddenInput)
    tagovi = forms.CharField(
        label='Tagovi', required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'npr. Apple, laptop, MacBook',
            'class': 'vTextField',
        }),
        help_text='Odvoji tagove zarezom. Za uklanjanje obriši naziv i sačuvaj artikal.',
    )

    class Meta:
        from .models import Product
        model = Product
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import json
        if 'stanje' in self.fields:
            self.fields['stanje'].widget.attrs['readonly'] = True
        if self.instance.pk:
            rows = list(self.instance.wms_zalihe.values('lokacija_id', 'kolicina'))
            if not rows and self.instance.wms_lokacija_id:
                rows = [{'lokacija_id': self.instance.wms_lokacija_id, 'kolicina': self.instance.stanje}]
            self.initial['wms_raspored'] = json.dumps(rows)
        if self.instance.pk:
            self.initial['tagovi'] = ', '.join(
                self.instance.tagovi.order_by('naziv').values_list('naziv', flat=True)
            )

    def clean(self):
        import json
        from .models import WMSLocation
        cleaned = super().clean()
        from .product360 import prepare_frames
        zip_upload = cleaned.get('upload_360_zip')
        images = cleaned.get('upload_360_images')
        if cleaned.get('delete_360') and (zip_upload or images):
            self.add_error('delete_360', 'Odaberite brisanje ili zamjenu seta.')
        elif zip_upload or images:
            try:
                self.prepared_360 = prepare_frames(zip_upload, images or [])
            except forms.ValidationError as exc:
                self.add_error('upload_360_zip' if zip_upload else 'upload_360_images', exc)
        raw = cleaned.get('wms_raspored')
        if raw:
            try:
                rows = json.loads(raw)
                if not isinstance(rows, list):
                    raise ValueError
                seen = set()
                for row in rows:
                    location = int(row['lokacija_id'])
                    quantity = row['kolicina']
                    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0 or location in seen:
                        raise ValueError
                    if not WMSLocation.objects.filter(pk=location).exists():
                        raise ValueError
                    seen.add(location)
                if sum(row['kolicina'] for row in rows) != cleaned.get('stanje', self.instance.stanje):
                    self.add_error('stanje', 'Zbir količina po lokacijama mora odgovarati ukupnoj količini.')
                cleaned['wms_rows'] = rows
            except (ValueError, TypeError, KeyError):
                self.add_error('stanje', 'Neispravan raspored količina po lokacijama.')
        elif 'stanje' in self.fields and cleaned.get('stanje', 0) and not cleaned.get('wms_lokacija'):
            self.add_error('wms_lokacija', 'Za unos količine obavezno odaberite WMS lokaciju.')
        return cleaned

    def clean_tagovi(self):
        names = []
        seen = set()
        for name in self.cleaned_data['tagovi'].split(','):
            name = name.strip()
            if not name:
                continue
            if len(name) > 50:
                raise forms.ValidationError('Svaki tag može imati najviše 50 znakova.')
            if name.casefold() not in seen:
                names.append(name)
                seen.add(name.casefold())
        return names

    def _save_m2m(self):
        from .product360 import replace_frames
        if hasattr(self, 'prepared_360'):
            replace_frames(self.instance, self.prepared_360)
        elif self.cleaned_data.get('delete_360'):
            replace_frames(self.instance, delete=True)
        from .models import Tag, ProductWMSStock
        rows = self.cleaned_data.get('wms_rows')
        if rows is None and self.cleaned_data.get('wms_lokacija'):
            rows = [{'lokacija_id': self.cleaned_data['wms_lokacija'].pk, 'kolicina': self.instance.stanje}]
        if rows is not None:
            self.instance.wms_zalihe.exclude(lokacija_id__in=[r['lokacija_id'] for r in rows]).delete()
            for row in rows:
                ProductWMSStock.objects.update_or_create(product=self.instance, lokacija_id=row['lokacija_id'], defaults={'kolicina': row['kolicina']})
        from .models import Tag
        names = self.cleaned_data.pop('tagovi', None)
        try:
            super()._save_m2m()
        finally:
            if names is not None:
                self.cleaned_data['tagovi'] = names
        if names is not None:
            self.instance.tagovi.set([Tag.get_or_create_by_name(name)[0] for name in names])
