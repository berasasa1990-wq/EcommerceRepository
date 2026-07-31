from django import forms
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User

import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from .models import (
    Akcija,
    AkcijaQtyTier,
    Banner,
    Brand,
    Category,
    Popup,
    Product,
    SiteSettings,
    Tag,
)

# Polja iz SiteSettings koja se uređuju na tipu „AI prodaja / AI dwell”
AI_SETTINGS_FIELD_NAMES = (
    'browse_interest_popup_aktivan',
    'browse_interest_popust',
    'product_dwell_popup_aktivan',
    'product_dwell_popust',
    'product_dwell_flash_seconds',
    'product_dwell_sale_pulse',
    'product_dwell_tag_text',
    'product_dwell_timer_label',
    'product_dwell_catalog_label',
    'product_dwell_boja_box',
    'product_dwell_boja_box2',
    'product_dwell_boja_border',
    'product_dwell_boja_accent',
    'product_dwell_boja_tag_tekst',
    'product_dwell_boja_tag_bg',
    'product_dwell_boja_timer_label',
    'product_dwell_boja_timer_bg',
    'product_dwell_boja_timer_tekst',
    'product_dwell_boja_stara_cijena',
    'product_dwell_boja_nova_cijena',
    'product_dwell_boja_nova_cijena_pulse',
    'product_dwell_boja_badge_bg',
    'product_dwell_boja_badge_tekst',
    'product_dwell_boja_kartica_bg',
    'product_dwell_boja_kartica_bg2',
    'product_dwell_boja_kartica_border',
    'product_dwell_boja_kartica_stara',
    'product_dwell_boja_kartica_nova',
    'product_dwell_boja_kartica_badge_bg',
    'product_dwell_boja_kartica_badge_tekst',
    'product_dwell_boja_kartica_label',
)


def _make_ai_settings_formfield(name):
    """Form field iz SiteSettings — deklarisan na AkcijaAdminForm (nije model Akcija)."""
    model_field = SiteSettings._meta.get_field(name)
    form_field = model_field.formfield()
    if form_field is None:
        form_field = forms.CharField(required=False, label=name)
    form_field.required = False
    if name.startswith('product_dwell_boja_'):
        form_field.widget = forms.TextInput(attrs={
            'type': 'color',
            'style': (
                'width:3.5rem;height:2.2rem;padding:2px;'
                'cursor:pointer;vertical-align:middle;'
            ),
        })
    return form_field


def _parse_flexible_number(value, *, field_label='Broj'):
    """Prihvati 10, 10.5, 10,5, 10%, ' 15 % '."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    s = str(value).strip()
    if not s:
        return None
    s = s.replace('%', '').replace(' ', '').replace(',', '.')
    # ostavi samo prvi broj
    m = re.search(r'-?\d+(?:\.\d+)?', s)
    if not m:
        raise forms.ValidationError(f'Unesite ispravan broj za {field_label} (npr. 10 ili 10,5).')
    try:
        return Decimal(m.group(0))
    except (InvalidOperation, ValueError):
        raise forms.ValidationError(f'Unesite ispravan broj za {field_label} (npr. 10 ili 10,5).')


class FlexibleDecimalField(forms.DecimalField):
    """Decimal polje koje prihvata zarez i znak %."""

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            return _parse_flexible_number(value, field_label=str(self.label or 'vrijednost'))
        except forms.ValidationError:
            raise
        except Exception:
            raise forms.ValidationError('Unesite ispravan broj (npr. 10 ili 10,5).')


class AkcijaQtyTierForm(forms.ModelForm):
    """Forma za količinski tier — tolerantna na 10% / 10,5."""

    popust_postotak = FlexibleDecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0.01'),
        max_value=Decimal('100'),
        label='Popust (%)',
        required=False,
        help_text='Npr. 10 ili 10,5 (bez obaveznog znaka %).',
    )
    quantity = forms.IntegerField(
        label='Kupi (komada)',
        required=False,
        min_value=1,
        help_text='Minimalno 2.',
    )

    class Meta:
        model = AkcijaQtyTier
        fields = ('quantity', 'popust_postotak', 'redoslijed')

    def _raw_qty_pct(self):
        if self.data is None or self.prefix is None:
            return '', ''
        p = self.prefix
        return (
            str(self.data.get(f'{p}-quantity', '') or '').strip(),
            str(self.data.get(f'{p}-popust_postotak', '') or '').strip(),
        )

    def has_changed(self):
        """Prazan red (bez količine i %) se ne smatra unosom — inače save puca."""
        qty_raw, pct_raw = self._raw_qty_pct()
        if not qty_raw and not pct_raw:
            return False
        return super().has_changed()

    def clean(self):
        cleaned = super().clean()
        qty = cleaned.get('quantity')
        pct = cleaned.get('popust_postotak')
        qty_raw, pct_raw = self._raw_qty_pct()
        # Prazan red (oba prazna) — OK, ne snima se
        if not qty_raw and not pct_raw:
            cleaned['quantity'] = None
            cleaned['popust_postotak'] = None
            return cleaned
        if qty in (None, ''):
            self.add_error('quantity', 'Unesite količinu (npr. 2).')
        elif int(qty) < 2:
            self.add_error('quantity', 'Količina mora biti barem 2.')
        if pct in (None, ''):
            self.add_error('popust_postotak', 'Unesite popust u % (npr. 10).')
        else:
            try:
                pct_dec = Decimal(pct)
            except Exception:
                pct_dec = None
            if pct_dec is not None and pct_dec <= 0:
                self.add_error('popust_postotak', 'Popust mora biti veći od 0.')
            elif pct_dec is not None and pct_dec > 100:
                self.add_error('popust_postotak', 'Popust ne može biti preko 100%.')
        return cleaned


class AkcijaAdminForm(forms.ModelForm):
    """
    Za „Kupi više”: jednostavna polja količina → %.
    Za „AI prodaja / AI dwell”: sva polja iz SiteSettings (kao stari zasebni meni).
    """

    qty_2_popust = FlexibleDecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0.01'),
        max_value=Decimal('100'),
        label='Kupi 2 komada — popust (%)',
        help_text='Npr. 10 = -10% kad kupac uzme 2 komada. Prazno = nema te opcije.',
    )
    qty_3_popust = FlexibleDecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0.01'),
        max_value=Decimal('100'),
        label='Kupi 3 komada — popust (%)',
        help_text='Npr. 20 = -20% za 3 komada.',
    )
    qty_4_popust = FlexibleDecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0.01'),
        max_value=Decimal('100'),
        label='Kupi 4 komada — popust (%)',
        help_text='Opcionalno.',
    )
    qty_5_popust = FlexibleDecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0.01'),
        max_value=Decimal('100'),
        label='Kupi 5 komada — popust (%)',
        help_text='Opcionalno.',
    )
    qty_6_popust = FlexibleDecimalField(
        required=False,
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0.01'),
        max_value=Decimal('100'),
        label='Kupi 6 komada — popust (%)',
        help_text='Opcionalno.',
    )

    class Meta:
        model = Akcija
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Jasne labele — artikle uvijek smiješ mijenjati (edit postojeće akcije)
        tip = getattr(self.instance, 'tip', None) if self.instance else None
        # Nikad ne zaključavaj polja artikala / popusta (ni na postojećem redu)
        for _lock_name in ('artikal', 'gratis_artikal', 'popust_postotak', 'tip'):
            if _lock_name in self.fields:
                self.fields[_lock_name].disabled = False
        if 'artikal' in self.fields:
            self.fields['artikal'].required = False
            self.fields['artikal'].label = '1. Trigger artikal'
            self.fields['artikal'].help_text = (
                'Možeš promijeniti bez brisanja akcije — pretraži i odaberi drugi, pa Sačuvaj. '
                '+ Ponuda: kad kupac doda OVAJ artikal u korpu, iskače popup. '
                'Kupi više: artikal na koji važi količinski %. '
                'Bundle: samo ako je trigger „odabrani trigger artikal”.'
            )
            if tip == Akcija.Tip.PONUDA:
                self.fields['artikal'].label = '1. Trigger (dodaj u korpu → popup)'
        if 'popust_postotak' in self.fields:
            self.fields['popust_postotak'].required = False
            self.fields['popust_postotak'].label = '2. Popust (%) — opcionalno'
            self.fields['popust_postotak'].help_text = (
                '+ Ponuda: % snizenja na DRUGI (ponuda) artikal kad kupac kaže DA. '
                'Prazno = redovna cijena (bez popusta). '
                'Bundle: % na set (ako linija nema svoj %).'
            )
            if tip == Akcija.Tip.PONUDA:
                self.fields['popust_postotak'].label = '2. Popust % na ponudu (opcionalno)'
        if 'gratis_artikal' in self.fields:
            self.fields['gratis_artikal'].required = False
            self.fields['gratis_artikal'].label = '3. Ponuda artikal (popup)'
            self.fields['gratis_artikal'].help_text = (
                'Možeš promijeniti bez brisanja akcije. '
                '+ Ponuda: artikal koji se nudi u popupu nakon dodavanja triggera '
                '(s % iz polja 2, ili bez %). Mora biti na stanju da se popup prikaže.'
            )
            if tip == Akcija.Tip.PONUDA:
                self.fields['gratis_artikal'].label = '3. Artikal u ponudi (sa ili bez %)'

        # Učitaj postojeće tierove u polja 2–6
        instance = getattr(self, 'instance', None)
        if instance and instance.pk and getattr(instance, 'tip', None) == Akcija.Tip.QTY_DEAL:
            for tier in instance.qty_tiers.all():
                try:
                    q = int(tier.quantity)
                except (TypeError, ValueError):
                    continue
                if 2 <= q <= 6:
                    field_name = f'qty_{q}_popust'
                    if field_name in self.fields and tier.popust_postotak is not None:
                        self.fields[field_name].initial = tier.popust_postotak

        # AI postavke — initial iz SiteSettings (polja su deklarisana na klasi)
        try:
            site = SiteSettings.load()
        except Exception:
            site = None
        if site is not None:
            for name in AI_SETTINGS_FIELD_NAMES:
                if name not in self.fields:
                    continue
                val = getattr(site, name, None)
                # None na % poljima → prikaži efektivni default (10) da admin vidi stvarno ponašanje
                if name in ('browse_interest_popust', 'product_dwell_popust') and val is None:
                    val = Decimal('10.00')
                self.fields[name].initial = val

        # Jasne labele za AI popup / popust (0 = bez snizenja)
        if 'browse_interest_popup_aktivan' in self.fields:
            self.fields['browse_interest_popup_aktivan'].label = (
                'AI prodaja aktivna (automatski popup)'
            )
            self.fields['browse_interest_popup_aktivan'].help_text = (
                'Uključeno: AI prati kupca i šalje popup ponudu (1–2 artikla). '
                'Isključeno: nema automatskog popupa.'
            )
        if 'browse_interest_popust' in self.fields:
            f = self.fields['browse_interest_popust']
            f.label = 'AI prodaja — popust (%)'
            f.help_text = (
                '0 = ponuda BEZ popusta (samo preporuka, redovna cijena). '
                'Npr. 5, 10, 15 = sniženje. Max 50.'
            )
            f.required = False
            if hasattr(f, 'min_value'):
                f.min_value = Decimal('0')
            if hasattr(f, 'max_value'):
                f.max_value = Decimal('50')
        if 'product_dwell_popust' in self.fields:
            f = self.fields['product_dwell_popust']
            f.label = 'AI dwell — default popust (%)'
            f.help_text = (
                'Fallback % za artikle bez unosa u tabeli. '
                '0 = bez popusta. Max 50.'
            )
            f.required = False
            if hasattr(f, 'min_value'):
                f.min_value = Decimal('0')

    def save_ai_settings(self):
        """Snimi AI polja u SiteSettings (singleton)."""
        if not hasattr(self, 'cleaned_data'):
            return None
        site = SiteSettings.load()
        changed = []
        for name in AI_SETTINGS_FIELD_NAMES:
            if name not in self.cleaned_data:
                continue
            val = self.cleaned_data.get(name)
            setattr(site, name, val)
            changed.append(name)
        if changed:
            site.save(update_fields=changed)
        return site

    def qty_deal_tiers_from_form(self):
        """Lista (quantity, popust) iz jednostavnih polja."""
        rows = []
        for q in (2, 3, 4, 5, 6):
            pct = self.cleaned_data.get(f'qty_{q}_popust')
            if pct is None or pct == '':
                continue
            try:
                pct_dec = Decimal(pct)
            except Exception:
                continue
            if pct_dec <= 0:
                continue
            rows.append((q, pct_dec))
        return rows

    def _allow_inactive_if_current(self, product, *, field_name):
        """Pri izmjeni akcije zadrži i neaktivan artikal ako je već bio odabran."""
        if not product or product.aktivan:
            return True
        inst = getattr(self, 'instance', None)
        if not inst or not inst.pk:
            return False
        current_id = getattr(inst, f'{field_name}_id', None)
        return current_id == product.pk

    def clean_artikal(self):
        artikal = self.cleaned_data.get('artikal')
        tip = self.cleaned_data.get('tip') or getattr(self.instance, 'tip', None)
        if tip == Akcija.Tip.BUNDLE:
            trigger = self.cleaned_data.get('bundle_trigger') or getattr(
                self.instance, 'bundle_trigger', None,
            )
            if trigger == Akcija.BundleTrigger.TRIGGER_PRODUCT and not artikal:
                raise forms.ValidationError('Odaberite trigger artikal.')
            if artikal and not artikal.aktivan and not self._allow_inactive_if_current(
                artikal, field_name='artikal',
            ):
                raise forms.ValidationError('Artikal mora biti aktivan na sajtu.')
            return artikal
        if tip == Akcija.Tip.QTY_DEAL and not artikal:
            raise forms.ValidationError('Odaberite artikal (možeš ga kasnije zamijeniti).')
        if tip == Akcija.Tip.PONUDA and not artikal:
            raise forms.ValidationError(
                'Odaberite trigger artikal — kad se doda u korpu, iskače ponuda.'
            )
        if tip == Akcija.Tip.AI_PRODAJA:
            return artikal
        if artikal and not artikal.aktivan and not self._allow_inactive_if_current(
            artikal, field_name='artikal',
        ):
            raise forms.ValidationError(
                'Artikal mora biti aktivan, ili zadrži postojeći odabir.'
            )
        return artikal

    def clean_gratis_artikal(self):
        gratis = self.cleaned_data.get('gratis_artikal')
        tip = self.cleaned_data.get('tip') or getattr(self.instance, 'tip', None)
        if tip == Akcija.Tip.PONUDA:
            if not gratis:
                raise forms.ValidationError(
                    'Odaberite ponudu artikal (drugi artikal u popupu, sa ili bez %).'
                )
            if not gratis.aktivan and not self._allow_inactive_if_current(
                gratis, field_name='gratis_artikal',
            ):
                raise forms.ValidationError(
                    'Ponuda artikal mora biti aktivan, ili zadrži postojeći odabir.'
                )
        elif gratis and not gratis.aktivan and not self._allow_inactive_if_current(
            gratis, field_name='gratis_artikal',
        ):
            raise forms.ValidationError('Artikal mora biti aktivan na sajtu.')
        return gratis

    def clean(self):
        cleaned = super().clean()
        tip = cleaned.get('tip') or getattr(self.instance, 'tip', None)
        if not tip:
            return cleaned

        if tip not in Akcija.ACTIVE_TIPS:
            self.add_error(
                'tip',
                'Dozvoljeni tipovi: Pop-up bundle, Kupi više, + Ponuda, AI prodaja / AI dwell.',
            )
            return cleaned

        if tip == Akcija.Tip.AI_PRODAJA:
            return cleaned

        if tip == Akcija.Tip.BUNDLE:
            # % na setu nije obavezan ako linije imaju svoj % (validacija u inline)
            trigger = cleaned.get('bundle_trigger') or Akcija.BundleTrigger.DELAY
            if trigger == Akcija.BundleTrigger.TRIGGER_PRODUCT and not cleaned.get('artikal'):
                self.add_error('artikal', 'Odaberite trigger artikal.')
            if trigger == Akcija.BundleTrigger.CATEGORY and not cleaned.get('kategorija'):
                self.add_error('kategorija', 'Odaberite trigger kategoriju.')

        elif tip == Akcija.Tip.QTY_DEAL:
            if not cleaned.get('artikal'):
                self.add_error('artikal', 'Odaberite artikal za količinski popust.')
            tiers = []
            for q in (2, 3, 4, 5, 6):
                pct = cleaned.get(f'qty_{q}_popust')
                if pct is not None and pct != '':
                    tiers.append(q)
            if not tiers:
                self.add_error(
                    'qty_2_popust',
                    'Unesi barem jedan popust — npr. kod „Kupi 2 komada” upiši 10, '
                    'ili kod „Kupi 3 komada” upiši 20.',
                )

        elif tip == Akcija.Tip.PONUDA:
            if not cleaned.get('artikal'):
                self.add_error(
                    'artikal',
                    'Odaberite trigger artikal (popup samo pri dodavanju u korpu).',
                )
            if not cleaned.get('gratis_artikal'):
                self.add_error('gratis_artikal', 'Odaberite artikal u ponudi.')
            trigger = cleaned.get('artikal')
            offer = cleaned.get('gratis_artikal')
            if trigger and offer and trigger.pk == offer.pk:
                self.add_error(
                    'gratis_artikal',
                    'Ponuda artikal mora biti drugačiji od trigger artikla.',
                )
            pct = cleaned.get('popust_postotak')
            if pct is not None and pct != '':
                try:
                    pct_dec = Decimal(pct)
                except Exception:
                    pct_dec = None
                if pct_dec is not None and pct_dec <= 0:
                    self.add_error('popust_postotak', 'Popust mora biti veći od 0, ili ostavi prazno.')
                elif pct_dec is not None and pct_dec > 100:
                    self.add_error('popust_postotak', 'Popust ne može biti preko 100%.')

        return cleaned

    def save_qty_deal_tiers(self, akcija):
        """
        Snimi polja 2–6 kom → %.
        Obrisano polje (prazno) = obriši taj tier iz baze i sačuvaj ostale.
        """
        if not akcija or not akcija.pk:
            return
        if akcija.tip != Akcija.Tip.QTY_DEAL:
            akcija.qty_tiers.all().delete()
            return

        # cleaned_data može nedostajati ako clean nije prošao — koristi data
        wanted = {}
        if hasattr(self, 'cleaned_data') and self.cleaned_data is not None:
            wanted = {q: pct for q, pct in self.qty_deal_tiers_from_form()}
        else:
            # fallback iz raw POST
            for q in (2, 3, 4, 5, 6):
                raw = self.data.get(f'qty_{q}_popust') if hasattr(self, 'data') else None
                if raw is None or str(raw).strip() == '':
                    continue
                try:
                    pct_dec = _parse_flexible_number(raw)
                except Exception:
                    continue
                if pct_dec is not None and pct_dec > 0:
                    wanted[q] = pct_dec

        # Eksplicitno obriši sve što nije više u formi (npr. obrisana opcija 3 kom)
        keep_qty = list(wanted.keys())
        if keep_qty:
            akcija.qty_tiers.exclude(quantity__in=keep_qty).delete()
        else:
            akcija.qty_tiers.all().delete()

        for q, pct in wanted.items():
            AkcijaQtyTier.objects.update_or_create(
                akcija=akcija,
                quantity=q,
                defaults={
                    'popust_postotak': pct,
                    'redoslijed': q,
                },
            )


# Deklariši AI polja na klasi (base_fields) — inače admin fieldsets → FieldError
for _ai_fname in AI_SETTINGS_FIELD_NAMES:
    try:
        _ai_ff = _make_ai_settings_formfield(_ai_fname)
    except Exception:
        continue
    AkcijaAdminForm.base_fields[_ai_fname] = _ai_ff
    AkcijaAdminForm.declared_fields[_ai_fname] = _ai_ff


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


def _turnstile_enabled():
    return bool(
        getattr(settings, 'TURNSTILE_SITE_KEY', '')
        and getattr(settings, 'TURNSTILE_SECRET_KEY', '')
    )


def _configure_turnstile_field(form):
    """Turnstile obavezan samo kad je konfigurisan (preskočen lokalno / DEBUG)."""
    field = form.fields.get('cf_turnstile_response')
    if not field:
        return
    if _turnstile_enabled():
        field.required = True
    else:
        field.required = False


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
        required=False,
        widget=forms.HiddenInput(),
        error_messages={
            'required': 'Molimo potvrdite da niste robot (Turnstile).'
        }
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _configure_turnstile_field(self)

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
        required=False,
        widget=forms.HiddenInput(),
        error_messages={
            'required': 'Molimo potvrdite da niste robot (Turnstile).'
        }
    )

    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)
        _configure_turnstile_field(self)

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
        label='Mobilni telefon',
        max_length=30,
        help_text=(
            'Obavezno mobilni broj (06…). '
            'Isti broj: 065666666 = +38765666666 = 0038765666666.'
        ),
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
        from .loyalty import telefon_vec_registrovan, validiraj_ba_mobilni

        telefon = self.cleaned_data.get('telefon', '').strip()
        try:
            local, _e164 = validiraj_ba_mobilni(telefon)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        if telefon_vec_registrovan(local):
            raise forms.ValidationError(
                'Ovaj broj je već na loyalty kartici '
                '(uključujući format +387 / 00387).'
            )
        # Vrati normalizirani 06… za spremanje
        return local

    def clean_email(self):
        from .loyalty import email_vec_registrovan

        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        if email_vec_registrovan(email):
            raise forms.ValidationError(
                'Ovaj email je već registrovan na loyalty karticu — '
                'dupli email nije dozvoljen.'
            )
        return email

    def clean(self):
        cleaned = super().clean()
        from .loyalty import email_vec_registrovan, telefon_vec_registrovan

        telefon = (cleaned.get('telefon') or '').strip()
        email = (cleaned.get('email') or '').strip().lower()
        if telefon and telefon_vec_registrovan(telefon):
            self.add_error(
                'telefon',
                'Ovaj broj je već registrovan (+387 / 00387 / 06… = isti).',
            )
        if email and email_vec_registrovan(email):
            self.add_error(
                'email',
                'Ovaj email je već registrovan — dupli email nije dozvoljen.',
            )
        return cleaned


class ProfileForm(forms.Form):
    """Korisnički nalog — email obavezan."""
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


class StaffLoyaltyProfileForm(forms.Form):
    """
    Staff: lični podaci na loyalty kartici — nijedno polje nije obavezno.
    Prazno = ne mijenjaj / sačuvaj prazno (osim kad se postavlja nova vrijednost).
    """
    ime_prezime = forms.CharField(
        label='Ime i prezime',
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Opcionalno',
        }),
    )
    email = forms.EmailField(
        label='Email',
        required=False,
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'Opcionalno',
        }),
    )
    telefon = forms.CharField(
        label='Telefon',
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Opcionalno',
        }),
    )
    adresa = forms.CharField(
        label='Adresa',
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Opcionalno',
        }),
    )
    grad = forms.CharField(
        label='Grad',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Opcionalno',
        }),
    )
    postanski_broj = forms.CharField(
        label='Poštanski broj',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Opcionalno',
        }),
    )

    def __init__(self, *args, exclude_user_id=None, **kwargs):
        self.exclude_user_id = exclude_user_id
        super().__init__(*args, **kwargs)

    def clean_email(self):
        from .loyalty import email_vec_registrovan

        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        if email_vec_registrovan(email, exclude_user_id=self.exclude_user_id):
            raise forms.ValidationError(
                'Ovaj email je već na drugoj loyalty kartici — dupli email nije dozvoljen.'
            )
        return email

    def clean_telefon(self):
        from .loyalty import telefon_vec_registrovan, validiraj_ba_mobilni

        telefon = (self.cleaned_data.get('telefon') or '').strip()
        if not telefon:
            return ''
        try:
            local, _e164 = validiraj_ba_mobilni(telefon)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        if telefon_vec_registrovan(local, exclude_user_id=self.exclude_user_id):
            raise forms.ValidationError(
                'Ovaj telefon je već na drugoj loyalty kartici '
                '(06… / +387… / 00387… = isti broj).'
            )
        return local


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
        label='Samo ažuriraj količine / stanje',
        required=False,
        initial=False,
        help_text=(
            'Samo količina kad je u Odoo-u > 0 (stavi na stanje na sajtu). '
            'Ako je u Odoo-u 0 — ne skida sa stanja na sajtu (artikli koje držiš samo na sajtu). '
            'Ne mijenja naziv, cijenu, slike. Ne kreira nove artikle.'
        ),
    )
    samo_naziv = forms.BooleanField(
        label='Samo ažuriraj naziv artikla',
        required=False,
        initial=False,
        help_text='Samo naziv artikla (i varijacija) iz Odoo-a ako se promijenio. Ne dirа količine, cijene, slike, kategoriju.',
    )
    samo_slike = forms.BooleanField(
        label='Samo ažuriraj slike artikla',
        required=False,
        initial=False,
        help_text='Samo slike iz Odoo-a za postojeće artikle. Ne mijenja naziv, količinu ni cijenu.',
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
        modes = [
            cleaned.get('samo_stanje'),
            cleaned.get('samo_naziv'),
            cleaned.get('samo_slike'),
        ]
        if sum(1 for m in modes if m) > 1:
            raise forms.ValidationError(
                'Odaberi samo jedan specijalni režim: količine, naziv ili slike.'
            )
        if cleaned.get('samo_stanje'):
            cleaned['azuriraj_postojece'] = True
            cleaned['ucitaj_slike'] = False
            cleaned['samo_slike'] = False
            cleaned['samo_naziv'] = False
        if cleaned.get('samo_naziv'):
            cleaned['azuriraj_postojece'] = True
            cleaned['ucitaj_slike'] = False
            cleaned['samo_stanje'] = False
            cleaned['samo_slike'] = False
        if cleaned.get('samo_slike'):
            cleaned['azuriraj_postojece'] = True
            cleaned['ucitaj_slike'] = True
            cleaned['samo_stanje'] = False
            cleaned['samo_naziv'] = False
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



