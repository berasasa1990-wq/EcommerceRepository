from django import forms
from .models import StoreSettings


class LogoForm(forms.ModelForm):
    class Meta:
        model = StoreSettings
        fields = ['logo']



from .models import Brand, Category, Product, Banner
from django.core.validators import MinValueValidator


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ['name', 'logo']


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'image', 'seo_title', 'seo_description']


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'image', 'description', 'price', 'original_price', 'brand', 'category', 'stock', 'is_out_of_stock', 'is_active', 'is_featured', 'seo_title', 'seo_description']

    def clean_price(self):
        value = self.cleaned_data['price']
        MinValueValidator(0)(value)
        return value

    def clean_original_price(self):
        value = self.cleaned_data.get('original_price')
        if value is not None:
            MinValueValidator(0)(value)
        return value


class BannerForm(forms.ModelForm):
    class Meta:
        model = Banner
        fields = ['position', 'title', 'eyebrow', 'subtitle', 'button_text', 'features', 'image', 'secondary_image', 'url', 'is_active']
        widgets = {'title': forms.Textarea(attrs={'rows': 2})}
        help_texts = {'image': 'Za pozicije „Glavni banner“ i „Veliki banner — ispod ponuda“ preporučena dimenzija slike je 1600 × 600 px (omjer 8:3). Prikazuje se cijela slika, bez rezanja. Automatsko spremanje u AVIF do 80 KB uz očuvanje što boljeg kvaliteta.'}

    def clean(self):
        data = super().clean()
        if data.get('position') != 'hero' and not data.get('image'):
            self.add_error('image', 'Dodajte sliku za ovu poziciju bannera.')
        return data

    def clean_url(self):
        value = self.cleaned_data['url'].strip()
        if value and not (value.startswith(('https://', 'http://', '#')) or (value.startswith('/') and not value.startswith('//'))):
            raise forms.ValidationError('Unesite lokalni link /... ili http(s):// adresu.')
        return value


class SaleProductSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if hasattr(value, 'instance'):
            product = value.instance
            option['attrs']['data-regular'] = '1' if product.is_active and not (product.original_price and product.original_price > product.price) else '0'
        return option


class LicensedSaleSelect(forms.Select):
    enabled_types = frozenset({'regular'})

    def create_option(self, *args, **kwargs):
        option = super().create_option(*args, **kwargs)
        if option['value'] and option['value'] not in self.enabled_types:
            option['attrs']['disabled'] = True
            option['label'] = str(option['label']) + ' — aktivacija u Moduli'
        return option


class SaleEndField(forms.DateTimeField):
    def to_python(self, value):
        from django.utils import timezone
        with timezone.override('Europe/Sarajevo'):
            return super().to_python(value)

    def prepare_value(self, value):
        from django.utils import timezone
        with timezone.override('Europe/Sarajevo'):
            return super().prepare_value(value)


class SaleForm(forms.ModelForm):
    sale_ends_at = SaleEndField(
        label='Akcija traje do', required=False,
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        help_text='Datum i vrijeme po lokalnom vremenu (Sarajevo). Prazno: bez vremenskog ograničenja.',
    )
    field_order = ['sale_type', 'product', 'discount_percent', 'price', 'plus_product', 'plus_percent', 'quantity_percent_2', 'quantity_percent_3', 'quantity_percent_over_3', 'bundle_products', 'bundle_percent', 'sale_ends_at']
    discount_percent = forms.DecimalField(label='Sniženje (%)', required=False, min_value=0.01, max_value=100, max_digits=5, decimal_places=2)
    product = forms.ModelChoiceField(queryset=Product.objects.all(), label='Artikal', widget=SaleProductSelect)

    class Meta:
        model = Product
        fields = ['sale_type', 'plus_product', 'plus_percent', 'quantity_percent_2', 'quantity_percent_3', 'quantity_percent_over_3', 'bundle_products', 'bundle_percent', 'price', 'original_price', 'sale_ends_at']
        labels = {'price': 'Nova cijena (KM)', 'original_price': 'Redovna cijena'}
        widgets = {'sale_ends_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}), 'sale_type': LicensedSaleSelect(), 'original_price': forms.HiddenInput(), 'bundle_products': forms.CheckboxSelectMultiple()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from bera_licensing.services import module_enabled
        self.enabled_sale_types = {'regular', *(code for code in ('article_plus', 'quantity', 'bundle') if module_enabled(code))}
        self.fields['sale_type'].widget.enabled_types = self.enabled_sale_types
        self.fields['sale_type'].help_text = 'Regularna akcija je FREE. Ponuda +, količinski popust i Bundle su plaćeni moduli i zahtijevaju aktivaciju u sekciji Moduli.'

        self.fields['plus_product'].queryset = Product.objects.filter(is_active=True)
        self.fields['bundle_products'].queryset = Product.objects.filter(is_active=True)
        self.fields['bundle_products'].label = 'Artikli u setu'
        self.fields['bundle_products'].help_text = 'Odaberite najmanje dva artikla. Set sadrži po jedan komad svakog artikla.'
        sale_type = self.data.get('sale_type') if self.is_bound else self.instance.sale_type
        if sale_type == 'bundle':
            self.fields['product'].required = False
            self.fields['product'].disabled = False
            if not self.is_bound and self.instance.pk:
                self.initial['bundle_products'] = [self.instance.pk, *self.instance.bundle_products.values_list('pk', flat=True)]
        self.is_plus = (self.data.get('sale_type') if self.is_bound else self.instance.sale_type) == 'article_plus'
        self.fields['price'].required = False
        self.fields['price'].min_value = 0
        if not self.is_bound and self.instance.sale_type != 'regular':
            self.initial['price'] = None

    def clean(self):
        data = super().clean()
        from django.utils import timezone
        if data.get('sale_ends_at') and data['sale_ends_at'] <= timezone.now():
            self.add_error('sale_ends_at', 'Odaberite datum i vrijeme u budućnosti.')
        if data.get('sale_type') in {'article_plus', 'quantity', 'bundle'} and data['sale_type'] not in self.enabled_sale_types:
            self.add_error('sale_type', 'Ova akcija zahtijeva kupljen i aktiviran modul. Otvorite sekciju Moduli i unesite kod za aktivaciju.')

        if self.is_plus:
            product = data.get('product')
            other = data.get('plus_product')
            percent = data.get('plus_percent')
            if not other:
                self.add_error('plus_product', 'Odaberite drugi artikal.')
            if product and other and product.pk == other.pk:
                self.add_error('plus_product', 'Odaberite artikal različit od prvog.')
            if product and (not product.is_active or (product.original_price and product.original_price > product.price)):
                self.add_error('product', 'Prvi artikal mora biti aktivan i imati regularnu cijenu.')
            if percent is None or not 0 < percent <= 100:
                self.add_error('plus_percent', 'Unesite procenat veći od 0 i do 100.')
            if product:
                data['price'] = product.price
                data['original_price'] = product.original_price
            return data
        data['plus_product'] = None
        data['plus_percent'] = None
        product = data.get('product')
        if data.get('sale_type') == 'bundle':
            members = data.get('bundle_products')
            if members is None or len(members) < 2:
                self.add_error('bundle_products', 'Odaberite najmanje dva artikla za set.')
            if data.get('bundle_percent') is None:
                self.add_error('bundle_percent', 'Unesite procenat sniženja seta.')
            if members:
                # Keep existing storage identity when editing; all members are equal in the UI.
                product = next((member for member in members if member.pk == self.instance.pk), members[0])
                data['product'] = product
                data['price'], data['original_price'] = product.price, product.original_price
            else:
                data['price'] = self.instance.price if self.instance.pk else 0
            return data
        if data.get('sale_type') == 'quantity':
            for name in ['quantity_percent_2', 'quantity_percent_3', 'quantity_percent_over_3']:
                if data.get(name) is None:
                    self.add_error(name, 'Unesite procenat od 0 do 100.')
            if product:
                data['price'] = product.original_price if product.original_price and product.original_price > product.price else product.price
                data['original_price'] = None
                if not product.is_active:
                    self.add_error('product', 'Odaberite aktivan artikal.')
            return data
        if data.get('sale_type') != 'regular':
            self.add_error('sale_type', 'Postavke ove vrste akcije još nisu definisane.')
            if product:
                data['price'], data['original_price'] = product.price, product.original_price
            return data
        if not product:
            return data
        from decimal import Decimal, ROUND_HALF_UP
        regular = product.original_price if product.original_price and product.original_price > product.price else product.price
        percent, price = data.get('discount_percent'), data.get('price')
        if percent is not None and price is not None:
            self.add_error(None, 'Unesite procenat sniženja ili novu cijenu, samo jedno od ta dva.')
        elif percent is not None:
            price = (regular * (1 - percent / Decimal('100'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        elif price is None:
            self.add_error(None, 'Unesite procenat sniženja ili novu cijenu.')
        if price is not None and not 0 <= price < regular:
            self.add_error('price', 'Nova cijena mora biti manja od redovne i ne može biti negativna.')
        data['price'] = price if price is not None else product.price
        data['original_price'] = regular

        return data


from django.contrib.auth.forms import AuthenticationForm


class PanelLoginForm(AuthenticationForm):
    username = forms.CharField(label='Korisničko ime')
    password = forms.CharField(label='Lozinka', strip=False, widget=forms.PasswordInput)
    error_messages = {
        'invalid_login': 'Korisničko ime ili lozinka nisu ispravni.',
        'inactive': 'Ovaj nalog nije aktivan.',
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not (user.is_staff or user.is_superuser):
            raise forms.ValidationError('Ovaj nalog nema pristup panelu.', code='no_panel_access')


from .models import Order


class CheckoutForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['customer_name', 'email', 'phone', 'address', 'city', 'postal_code', 'note']
        widgets = {'note': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        autocomplete = ['name', 'email', 'tel', 'street-address', 'address-level2', 'postal-code']
        for name, hint in zip(self.Meta.fields, autocomplete):
            self.fields[name].required = True
            self.fields[name].widget.attrs['autocomplete'] = hint


class StoreSettingsForm(forms.ModelForm):
    class Meta:
        model = StoreSettings
        fields = ['delivery_price', 'free_delivery_threshold']


class FeaturedProductsForm(forms.Form):
    products = forms.ModelMultipleChoiceField(
        label='Artikli za izdvojenu ponudu',
        queryset=Product.objects.filter(is_active=True).order_by('name'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='Označeni artikli prikazuju se na početnoj stranici kao izdvojeni.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial['products'] = Product.objects.filter(is_active=True, is_featured=True)

    def save(self):
        from django.db import transaction
        with transaction.atomic():
            Product.objects.filter(is_featured=True).update(is_featured=False)
            Product.objects.filter(pk__in=self.cleaned_data['products']).update(is_featured=True)


from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model


def normalize_customer_phone(value):
    import re
    value = value.strip()
    if not re.fullmatch(r'[+0-9 ()/.-]+', value) or ('+' in value and not value.startswith('+')) or value.count('+') > 1:
        raise forms.ValidationError('Unesite ispravan broj telefona.')
    digits = re.sub(r'[^0-9]', '', value)
    if digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0') and not value.startswith('+'):
        digits = '387' + digits[1:]
    if not 8 <= len(digits) <= 15 or digits.startswith('0'):
        raise forms.ValidationError('Unesite ispravan broj telefona s pozivnim brojem.')
    return '+' + digits


class CustomerContactFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            from .models import CustomerContact
            contact = CustomerContact.objects.filter(customer=self.instance).first()
            if contact:
                self.initial['phone'] = contact.phone

    def clean_email(self):
        from .models import CustomerContact
        email = self.cleaned_data['email'].strip().lower()
        if (get_user_model().objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists() or
                CustomerContact.objects.filter(email__iexact=email).exclude(customer_id=self.instance.pk).exists()):
            raise forms.ValidationError('Ovaj e-mail je već registrovan.')
        return email

    def clean_phone(self):
        from .models import CustomerContact
        phone = normalize_customer_phone(self.cleaned_data['phone'])
        if CustomerContact.objects.filter(phone=phone).exclude(customer_id=self.instance.pk).exists():
            raise forms.ValidationError('Ovaj telefon je već registrovan.')
        return phone

    def save(self, commit=True):
        from django.db import transaction
        from .models import CustomerContact
        with transaction.atomic():
            user = super().save(commit=commit)
            if commit:
                CustomerContact.objects.update_or_create(customer=user, defaults={
                    'phone': self.cleaned_data['phone'], 'email': self.cleaned_data['email'],
                })
            return user


class CustomerRegistrationForm(CustomerContactFormMixin, UserCreationForm):
    phone = forms.CharField(label='Telefon', max_length=40, widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel', 'placeholder': '+387 61 123 456'}))
    email = forms.EmailField(label='E-mail')

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ['first_name', 'last_name', 'email', 'phone']
        labels = {'first_name': 'Ime', 'last_name': 'Prezime'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].label = 'Lozinka'
        self.fields['password2'].label = 'Potvrdi lozinku'
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'

    def save(self, commit=True):
        import uuid
        if not self.instance.username:
            self.instance.username = 'customer_' + uuid.uuid4().hex
        return super().save(commit=commit)


class CustomerProfileForm(CustomerContactFormMixin, forms.ModelForm):
    phone = forms.CharField(label='Telefon', max_length=40, widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel'}))
    email = forms.EmailField(label='E-mail')

    class Meta:
        model = get_user_model()
        fields = ['first_name', 'last_name', 'email', 'phone']
        labels = {'first_name': 'Ime', 'last_name': 'Prezime'}


class CustomerLoginForm(AuthenticationForm):
    username = forms.EmailField(label='E-mail', widget=forms.EmailInput(attrs={'autocomplete': 'username', 'autofocus': True}))
    password = forms.CharField(label='Lozinka', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))
    error_messages = {'invalid_login': 'E-mail ili lozinka nisu ispravni.', 'inactive': 'Ovaj nalog nije aktivan.'}

    def clean(self):
        from django.contrib.auth import authenticate
        email = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')
        if email and password:
            users = list(get_user_model().objects.filter(email__iexact=email.strip())[:2])
            if len(users) != 1:
                raise self.get_invalid_login_error()
            self.user_cache = authenticate(self.request, username=users[0].username, password=password)
            if self.user_cache is None:
                raise self.get_invalid_login_error()
            self.confirm_login_allowed(self.user_cache)
        return self.cleaned_data


from django.forms import BaseModelFormSet, modelformset_factory
from .models import LoyaltyTier


class LoyaltyTierFormSet(BaseModelFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        ranges = []
        for form in self.forms:
            data = form.cleaned_data
            if not data or data.get('DELETE'):
                continue
            low, high = data['minimum'], data.get('maximum')
            if high is not None and high <= low:
                raise forms.ValidationError('Iznos do mora biti veći od iznosa od.')
            ranges.append((low, high))
        ranges.sort(key=lambda r: r[0])
        for previous, current in zip(ranges, ranges[1:]):
            if previous[1] is None or current[0] < previous[1]:
                raise forms.ValidationError('Rasponi potrošnje se ne smiju preklapati.')


LoyaltySettingsFormSet = modelformset_factory(LoyaltyTier, fields=['minimum', 'maximum', 'percent'], formset=LoyaltyTierFormSet, extra=1, can_delete=True)


from django.contrib.auth.forms import PasswordChangeForm


class CustomerPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, label in [('old_password', 'Trenutna lozinka'), ('new_password1', 'Nova lozinka'), ('new_password2', 'Potvrdi novu lozinku')]:
            self.fields[name].label = label


from .models import SEOSettings


class SEOSettingsForm(forms.ModelForm):
    class Meta:
        model = SEOSettings
        exclude = ['id']
        widgets = {'description': forms.Textarea(attrs={'rows': 3}), 'social_description': forms.Textarea(attrs={'rows': 3})}

    def clean_google_review_url(self):
        from urllib.parse import urlsplit
        value = self.cleaned_data['google_review_url'].strip()
        if value:
            parts = urlsplit(value)
            if parts.scheme != 'https' or parts.username or parts.password:
                raise forms.ValidationError('Unesite HTTPS link za Google recenzije, bez pristupnih podataka.')
        return value

    def clean_canonical_domain(self):
        from urllib.parse import urlsplit
        value = self.cleaned_data['canonical_domain'].strip().rstrip('/')
        if value:
            parts = urlsplit(value)
            if parts.scheme not in ('http', 'https') or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username or parts.password:
                raise forms.ValidationError('Unesite samo http(s) adresu domene bez putanje, parametara ili pristupnih podataka.')
        return value

    def clean_twitter_handle(self):
        import re
        value = self.cleaned_data['twitter_handle'].strip()
        if value and not re.fullmatch(r'@?[A-Za-z0-9_]{1,15}', value):
            raise forms.ValidationError('Unesite ispravno korisničko ime, do 15 znakova.')
        return '@' + value.lstrip('@') if value else ''

    def clean(self):
        data = super().clean()
        for name in ('social_image', 'favicon'):
            image = data.get(name)
            if image and hasattr(image, 'image') and image.image.format not in ('JPEG', 'PNG'):
                self.add_error(name, 'Učitajte JPG ili PNG sliku.')
        return data


from .models import ChatBotSettings


class ChatBotSettingsForm(forms.ModelForm):
    api_key = forms.CharField(label='OpenAI API ključ', required=False, max_length=512, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}), help_text='Unesite novi ključ za zamjenu. Prazno zadržava postojeći. Ključ se čuva šifriran i nikada se ne prikazuje kupcima.')
    remove_api_key = forms.BooleanField(label='Ukloni spremljeni API ključ', required=False)

    class Meta:
        model = ChatBotSettings
        exclude = ['id', 'encrypted_api_key']
        widgets = {'instructions': forms.Textarea(attrs={'rows': 4}), 'knowledge': forms.Textarea(attrs={'rows': 6}), 'suggested_questions': forms.Textarea(attrs={'rows': 4})}

    field_order = ['enabled', 'name', 'welcome_message', 'auto_open', 'delay_seconds', 'guided_enabled', 'category_question', 'budget_question', 'budget_presets', 'results_message', 'no_results_message', 'support_email', 'instructions', 'knowledge', 'suggested_questions', 'model_name', 'daily_request_limit', 'api_key', 'remove_api_key']

    def clean_budget_presets(self):
        from decimal import Decimal, InvalidOperation
        value = self.cleaned_data['budget_presets']
        try:
            amounts = [Decimal(part.strip()) for part in value.split(',') if part.strip()]
            if len(amounts) > 8 or any(not amount.is_finite() or amount < 0 or amount > Decimal('99999999.99') or amount.as_tuple().exponent < -2 for amount in amounts):
                raise ValueError
        except (InvalidOperation, ValueError):
            raise forms.ValidationError('Unesite do 8 budžeta u KM, odvojenih zarezom (npr. 300, 500, 1000).')
        return ', '.join(format(amount, 'f') for amount in amounts)

    def clean_api_key(self):
        value = self.cleaned_data['api_key'].strip()
        if any(c.isspace() for c in value):
            raise forms.ValidationError('API ključ ne smije sadržavati razmake ili nove redove.')
        return value

    def clean_model_name(self):
        import re
        value = self.cleaned_data['model_name'].strip()
        if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,100}', value):
            raise forms.ValidationError('Unesite ispravan naziv modela.')
        return value

    def save(self, commit=True):
        from .chatbot import key_cipher
        instance = super().save(commit=False)
        if self.cleaned_data.get('remove_api_key'):
            instance.encrypted_api_key = ''
        if self.cleaned_data.get('api_key'):
            instance.encrypted_api_key = key_cipher().encrypt(self.cleaned_data['api_key'].encode()).decode()
        if commit:
            instance.save()
        return instance


from .models import LoyaltyRules


class LoyaltyFeaturesForm(forms.ModelForm):
    class Meta:
        model = LoyaltyRules
        fields = ['tiers_enabled', 'points_enabled']


class LoyaltyPointsSettingsForm(forms.ModelForm):
    class Meta:
        model = LoyaltyRules
        fields = ['award_spend', 'points_per_km', 'redemption_points', 'point_value', 'max_discount_percent']
        labels = {
            'award_spend': 'Iznos kupovine (KM)',
            'points_per_km': 'Dodijeli bodova',
            'redemption_points': 'Broj bodova za popust',
            'point_value': 'Iznos popusta (KM)',
            'max_discount_percent': 'Najveći popust bodovima (%)',
        }


class SEODescriptionForm(forms.ModelForm):
    class Meta:
        model = SEOSettings
        fields = ['description']
        widgets = {'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Kratko opišite ponudu svoje trgovine…'})}

    def clean_description(self):
        description = self.cleaned_data['description'].strip()
        if not description:
            raise forms.ValidationError('Unesite meta opis sajta.')
        return description
