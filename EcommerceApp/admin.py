import logging

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, mark_safe
from django.middleware.csrf import get_token

from .forms import (
    AkcijaAdminForm,
    AkcijaQtyTierForm,
    BannerAdminForm,
    BulkAssignBrandForm,
    BulkAssignCategoryForm,
    MergeProductsForm,
    OdooImportForm,
    PopupAdminForm,
)
from .odoo_client import OdooClient, OdooError, odoo_je_konfigurisan
from .odoo_import import (
    fetch_template_ids_from_odoo,
    import_chunk_size,
    import_products_from_odoo,
    merge_import_stats,
    _empty_import_stats,
)

logger = logging.getLogger(__name__)
ODOO_IMPORT_SESSION_KEY = 'odoo_import_job'
from .product_merge import ProductMergeError, merge_products, split_product_variations
from .models import (
    ActiveCartItem,
    AdvisorBeginnerFishType,
    AdvisorBeginnerSet,
    AdvisorBeginnerSetItem,
    AIProdajaSettings,
    ProductDwellItem,
    AkcijaBundleLine,
    AkcijaQtyTier,
    CityVisitTotal,
    LiveVisitor,
    LiveVisitorOffer,
    StaffSiteEvent,
    Akcija,
    Banner,
    Brand,
    Category,
    ChatConversation,
    ChatMessage,
    Coupon,
    HomeBrandShowcase,
    HomeCategoryShowcase,
    HomeFeaturedProduct,
    HomeNovoProduct,
    HomePromoCard,
    HomeTrustItem,
    HomeVlog,
    LoyaltyCard,
    Order,
    OrderItem,
    Popup,
    OnlineGiftCampaign,
    OnlineGiftClaim,
    OnlineGiftPush,
    PageSEO,
    Product,
    ProductImage,
    ProductVariation,
    SiteSettings,
    Tag,
    UpsellOffer,
    UserProfile,
    ProductWarehouseMeta,
    WarehouseLocation,
    WarehouseMovement,
    WarehouseStock,
    WarehouseCustomer,
    WarehouseSupplier,
    WarehouseSyncLog,
    MagacinDeklaracijaBrend,
)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        'product_naziv', 'varijacija_naziv', 'sifra',
        'bazna_cijena', 'cijena', 'kolicina',
        'popust_opis', 'popust_postotak', 'popust_iznos',
    )
    fields = (
        'product_naziv', 'varijacija_naziv', 'sifra', 'kolicina',
        'bazna_cijena', 'cijena', 'popust_opis', 'popust_postotak', 'popust_iznos',
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'broj', 'korisnik', 'ime_prezime', 'email', 'telefon',
        'ukupno', 'status', 'izvor', 'lager_status', 'odstampana', 'zapakovana', 'kreirana',
    )
    list_filter = ('status', 'izvor', 'lager_status', 'odstampana', 'zapakovana', 'kreirana')
    search_fields = ('broj', 'ime_prezime', 'email', 'telefon', 'korisnik__email')
    readonly_fields = (
        'broj', 'kreirana', 'medjuzbir', 'dostava', 'popust',
        'ukupno', 'popust_detalji', 'odstampana', 'odstampana_at',
    )
    autocomplete_fields = ('korisnik',)
    inlines = [OrderItemInline]
    fieldsets = (
        ('Narudžba', {
            'fields': (
                'broj', 'status', 'izvor', 'lager_status', 'medjuzbir', 'popust', 'kupon_kod',
                'popust_detalji', 'dostava', 'ukupno', 'kreirana',
                'odstampana', 'odstampana_at',
            ),
        }),
        ('Kupac', {'fields': ('korisnik', 'ime_prezime', 'email', 'telefon')}),
        ('Dostava', {'fields': ('adresa', 'grad', 'postanski_broj', 'napomena')}),
    )


@admin.register(LoyaltyCard)
class LoyaltyCardAdmin(admin.ModelAdmin):
    list_display = ('user', 'kod', 'nivo', 'ukupna_potrosnja', 'azurirana')
    list_filter = ('nivo',)
    search_fields = ('kod', 'barkod', 'user__email', 'user__first_name')
    readonly_fields = ('kreirana', 'azurirana')
    autocomplete_fields = ('user',)


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('kod', 'naziv', 'postotak', 'vlasnik', 'aktivan', 'automatski')
    list_filter = ('aktivan', 'automatski')
    search_fields = ('kod', 'naziv', 'vlasnik__email')
    autocomplete_fields = ('vlasnik', 'loyalty_kartica')
    readonly_fields = ('kreiran',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'telefon', 'grad')
    search_fields = ('user__email', 'user__first_name', 'telefon')
    autocomplete_fields = ('user',)


class ProductVariationInline(admin.TabularInline):
    model = ProductVariation
    extra = 1
    fields = (
        'naziv', 'sifra', 'slika', 'cijena', 'pakovanje_komada',
        'akcija_postotak', 'akcijska_cijena',
        'na_stanju', 'stanje', 'redoslijed', 'odoo_template_id', 'pregled_slike',
    )
    readonly_fields = ('odoo_template_id', 'pregled_slike')

    @admin.display(description='Pregled')
    def pregled_slike(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="height:50px;border-radius:4px;" />',
                obj.slika.url,
            )
        return '—'


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 3
    fields = ('slika', 'redoslijed', 'pregled_slike')
    readonly_fields = ('pregled_slike',)
    verbose_name = 'Dodatna slika'
    verbose_name_plural = 'Dodatne slike (prikazuju se ispod glavne na stranici artikla)'

    @admin.display(description='Pregled')
    def pregled_slike(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="height:50px;border-radius:4px;" />',
                obj.slika.url,
            )
        return '—'


class SiteSettingsAdminForm(forms.ModelForm):
    """
    Snimanje Podešavanja: opciona polja ne smiju blokirati Save,
    URL-ovi se čiste, predugački SEO se skraćuje s jasnom porukom.
    """

    class Meta:
        model = SiteSettings
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Nikad ne blokiraj Save zbog ovih tekstova (sklopljeni fieldset)
        for name in (
            'politika_dostava', 'politika_povrat', 'politika_garancija',
            'seo_title', 'meta_description', 'seo_organizacija_naziv',
            'seo_email', 'seo_grad', 'seo_drzava',
            'seo_facebook_url', 'seo_instagram_url',
            'google_site_verification', 'seo_title_suffix',
            'korpa_exit_popup_naslov', 'korpa_exit_popup_tekst',
        ):
            if name in self.fields:
                self.fields[name].required = False
        if 'korpa_exit_popup_artikal' in self.fields:
            self.fields['korpa_exit_popup_artikal'].required = False
            self.fields['korpa_exit_popup_artikal'].help_text = (
                'Opcionalno. Ako artikal više ne postoji, ostavi prazno i snimi.'
            )

    def _clean_optional_url(self, field_name):
        value = (self.cleaned_data.get(field_name) or '').strip()
        if not value:
            return ''
        if value.startswith(('http://', 'https://')):
            return value
        # Dozvoli unose bez scheme (instagram.com/… → https://…)
        if '.' in value and ' ' not in value:
            return 'https://' + value.lstrip('/')
        raise forms.ValidationError(
            'Unesi puni URL (npr. https://www.facebook.com/tvoja-stranica) ili ostavi prazno.',
        )

    def clean_seo_facebook_url(self):
        return self._clean_optional_url('seo_facebook_url')

    def clean_seo_instagram_url(self):
        return self._clean_optional_url('seo_instagram_url')

    def clean_seo_title(self):
        value = (self.cleaned_data.get('seo_title') or '').strip()
        if len(value) > 70:
            value = value[:70]
        return value

    def clean_meta_description(self):
        value = (self.cleaned_data.get('meta_description') or '').strip()
        if len(value) > 160:
            value = value[:160]
        return value

    def clean_korpa_exit_popup_artikal(self):
        return self.cleaned_data.get('korpa_exit_popup_artikal')

    def full_clean(self):
        super().full_clean()
        # Nevažeći exit-artikal (obrisan / van izbora) ne smije blokirati Save
        if self._errors and 'korpa_exit_popup_artikal' in self._errors:
            self._errors.pop('korpa_exit_popup_artikal', None)
            if hasattr(self, 'cleaned_data'):
                self.cleaned_data['korpa_exit_popup_artikal'] = None


class _HomeInlineMixin:
    """Tabele na Podešavanjima — samo postojeći redovi (bez praznog „extra” reda)."""

    extra = 0  # ne otvaraj prazan red automatski
    show_change_link = False
    can_delete = True

    def has_add_permission(self, request, obj=None):
        return True

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return True

    def has_view_permission(self, request, obj=None):
        return True


class HomeTrustItemInline(_HomeInlineMixin, admin.TabularInline):
    model = HomeTrustItem
    fk_name = 'postavke'
    max_num = 6
    fields = ('redoslijed', 'naslov', 'podnaslov', 'ikona', 'aktivan')
    ordering = ('redoslijed', 'id')
    verbose_name = 'Trust stavka'
    verbose_name_plural = (
        '① TRUST TRAKA (ispod hero banera) — Brza dostava, Sigurna kupovina… '
        'Samo redovi koje dodaš (Add another). Prazan red se ne otvara sam.'
    )


class HomeFeaturedProductInline(_HomeInlineMixin, admin.TabularInline):
    model = HomeFeaturedProduct
    fk_name = 'postavke'
    max_num = 10
    autocomplete_fields = ('artikal',)
    fields = ('redoslijed', 'artikal', 'aktivan')
    ordering = ('redoslijed', 'id')
    verbose_name = 'Izdvojeni artikal'
    verbose_name_plural = (
        '② IZDVOJENI ARTIKLI (do 10) — ili označi artikal kao HIT na artiklu'
    )

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if 'artikal' in formset.form.base_fields:
            formset.form.base_fields['artikal'].help_text = (
                'Pretraži postojeći artikal. Redoslijed = red u karuselu na početnoj.'
            )
            formset.form.base_fields['artikal'].required = False
        return formset


class HomeNovoProductInline(_HomeInlineMixin, admin.TabularInline):
    model = HomeNovoProduct
    fk_name = 'postavke'
    max_num = 10
    autocomplete_fields = ('artikal',)
    fields = ('redoslijed', 'artikal', 'aktivan')
    ordering = ('redoslijed', 'id')
    verbose_name = 'Novitet'
    verbose_name_plural = (
        '③ NOVITETI (ručni odabir, do 10) — samo ako je način prikaza „Ručno” iznad'
    )

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if 'artikal' in formset.form.base_fields:
            formset.form.base_fields['artikal'].help_text = (
                'Pretraži artikal. Alternativa: označi „Noviteti” na samom artiklu.'
            )
            formset.form.base_fields['artikal'].required = False
        return formset


class HomePromoCardInline(_HomeInlineMixin, admin.StackedInline):
    model = HomePromoCard
    fk_name = 'postavke'
    max_num = 8
    fields = (
        'redoslijed', 'aktivan',
        'naslov', 'boja_naslova',
        'podnaslov', 'boja_podnaslova',
        'badge', 'boja_isticanja',
        'slika', 'ikona',
        'link',
    )
    ordering = ('redoslijed', 'id')
    verbose_name = 'Promo kartica'
    verbose_name_plural = (
        '④ PROMO KARTICE (ispod Akcijske ponude) — '
        'boje teksta (hex picker), slika 200×200 PNG ili ikona. '
        'Naslov: Enter za 2 reda. Badge: 150 KM / DO 10% / 50 KM…'
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name in ('boja_naslova', 'boja_podnaslova', 'boja_isticanja') and formfield:
            formfield.widget.input_type = 'color'
            formfield.widget.attrs.setdefault('style', 'width: 4rem; height: 2rem; padding: 0;')
        return formfield

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if 'naslov' in formset.form.base_fields:
            # prazan extra red ne smije padati validaciju
            formset.form.base_fields['naslov'].required = False
        return formset


class HomeCategoryShowcaseInline(_HomeInlineMixin, admin.TabularInline):
    model = HomeCategoryShowcase
    fk_name = 'postavke'
    autocomplete_fields = ('kategorija',)
    fields = ('redoslijed', 'kategorija', 'naslov', 'aktivan')
    ordering = ('redoslijed', 'id')
    verbose_name = 'Kategorija (2×2)'
    verbose_name_plural = '⑤ KATEGORIJE NA POČETNOJ (opcionalno, 2×2 mobil)'

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if 'kategorija' in formset.form.base_fields:
            formset.form.base_fields['kategorija'].help_text = (
                'Kategorija čiji se artikli prikazuju.'
            )
            formset.form.base_fields['kategorija'].required = False
        if 'naslov' in formset.form.base_fields:
            formset.form.base_fields['naslov'].help_text = 'Prazno = naziv kategorije.'
        return formset


class HomeBrandShowcaseInline(_HomeInlineMixin, admin.TabularInline):
    model = HomeBrandShowcase
    fk_name = 'postavke'
    autocomplete_fields = ('brend',)
    fields = ('redoslijed', 'brend', 'naslov', 'aktivan')
    ordering = ('redoslijed', 'id')
    verbose_name = 'Brend (slide)'
    verbose_name_plural = '⑥ BREND KARUSELI ARTIKALA (opcionalno)'

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        if 'brend' in formset.form.base_fields:
            formset.form.base_fields['brend'].help_text = 'Brend za karusel artikala.'
            formset.form.base_fields['brend'].required = False
        if 'naslov' in formset.form.base_fields:
            formset.form.base_fields['naslov'].help_text = 'Prazno = naziv brenda.'
        return formset


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    # Duga forma (inlines + SEO) — Save mora biti lako dostupan
    form = SiteSettingsAdminForm
    save_on_top = True
    change_form_template = 'admin/EcommerceApp/sitesettings/change_form.html'
    autocomplete_fields = ('korpa_exit_popup_artikal',)
    readonly_fields = (
        'pregled_loga', 'pregled_loga_glavnog_sajta', 'pregled_favicona',
        'pregled_badgea', 'pregled_chat_avatara',
    )
    inlines = [
        HomeTrustItemInline,
        HomeFeaturedProductInline,
        HomeNovoProductInline,
        HomePromoCardInline,
        HomeCategoryShowcaseInline,
        HomeBrandShowcaseInline,
    ]

    class Media:
        css = {
            'all': ('admin/css/ozr_admin.css',),
        }

    def get_inline_instances(self, request, obj=None):
        # Uvijek vrati svih 6 tabela (ne filtriraj)
        instances = super().get_inline_instances(request, obj)
        # Ako bi nešto vratilo prazno, forsira rekonstrukciju
        if not instances and self.inlines:
            instances = [
                inline(self.model, self.admin_site) for inline in self.inlines
            ]
        return instances

    def get_formset_kwargs(self, request, obj, inline, prefix):
        """
        Ako browser ne pošalje TOTAL_FORMS (tabele nisu u HTML-u / JS ih skine),
        popuni POST iz baze — Save prođe i NE briše trust/promo/izdvojene.
        """
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        if request.method != 'POST' or obj is None:
            return kwargs
        data = kwargs.get('data')
        if data is None:
            return kwargs
        total_key = f'{prefix}-TOTAL_FORMS'
        if total_key in data:
            return kwargs
        # Mutable copy
        data = data.copy()
        try:
            self._inject_inline_management_from_db(data, request, obj, inline, prefix)
            kwargs['data'] = data
        except Exception:
            logger.exception(
                'SiteSettings: nije uspjelo injektovanje management forme za %s',
                prefix,
            )
        return kwargs

    def _inject_inline_management_from_db(self, data, request, obj, inline, prefix):
        """Popuni TOTAL/INITIAL + polja postojećih redova iz baze."""
        FormSet = inline.get_formset(request, obj)
        fs = FormSet(
            instance=obj,
            prefix=prefix,
            queryset=inline.get_queryset(request),
        )
        n = fs.initial_form_count()
        data[f'{prefix}-TOTAL_FORMS'] = str(n)
        data[f'{prefix}-INITIAL_FORMS'] = str(n)
        data[f'{prefix}-MIN_NUM_FORMS'] = '0'
        max_num = fs.max_num
        data[f'{prefix}-MAX_NUM_FORMS'] = str(max_num if max_num is not None else 1000)

        fk_name = getattr(fs, 'fk', None)
        fk_name = fk_name.name if fk_name is not None else 'postavke'

        for i, form in enumerate(fs.initial_forms):
            inst = form.instance
            if not inst or not inst.pk:
                continue
            data[f'{prefix}-{i}-id'] = str(inst.pk)
            data[f'{prefix}-{i}-{fk_name}'] = str(obj.pk)
            for name, field in form.fields.items():
                key = f'{prefix}-{i}-{name}'
                # FileField: ne diraj — ModelForm zadrži postojeću sliku
                if isinstance(field, forms.FileField):
                    continue
                if isinstance(field, forms.BooleanField):
                    if getattr(inst, name, False):
                        data[key] = 'on'
                    continue
                val = getattr(inst, name, None)
                if val is None:
                    data[key] = ''
                elif hasattr(val, 'pk'):
                    data[key] = str(val.pk)
                else:
                    data[key] = str(val)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        # Osiguraj mutable POST prije formseta (QueryDict copy)
        if request.method == 'POST':
            try:
                request.POST = request.POST.copy()
            except Exception:
                pass
        return super().changeform_view(
            request, object_id=object_id, form_url=form_url, extra_context=extra_context,
        )

    fieldsets = (
        ('① Logo i izgled sajta', {
            'fields': (
                'logo', 'pregled_loga',
                'logo_glavni_sajt', 'pregled_loga_glavnog_sajta',
                'favicon', 'pregled_favicona',
            ),
            'description': (
                'Logo u crnom headeru (originalna slika, max 640×128). '
                'Preporuka: PNG s transparentnom pozadinom, bijela/zelena grafika.'
            ),
        }),
        ('② Početna — naslovi sekcija (redom na stranici)', {
            'fields': (
                'promo_bar_tekst', 'promo_bar_link_tekst',
                'naslov_izdvojeno', 'podnaslov_izdvojeno',
                'naslov_novo', 'podnaslov_novo', 'noviteti_mod',
                'naslov_akcija', 'podnaslov_akcija', 'prikazi_akcijsku_sekciju',
                'naslov_blog',
                'naslov_brendovi',
                'tekst_pogledaj_sve',
            ),
            'description': (
                'Redoslijed na početnoj:\n'
                'Hero baner → Trust traka (tabela ①) → Izdvojeni (②) → Noviteti (③) → '
                'Akcijska ponuda → Promo kartice (④) → Blog + Newsletter → Brendovi.\n'
                '• Naslov u 2 reda: stavi Enter u polju (npr. BESPLATNA ↵ DOSTAVA u promo karticama).\n'
                '• Izdvojeni: HIT na artiklu ili tabela ②.\n'
                '• Noviteti: je_novitet na artiklu / auto / ručno (③).\n'
                '• Akcijska: artikli sa sniženom cijenom (uključi/isključi ispod).'
            ),
        }),
        ('③ Početna — Newsletter (pored bloga)', {
            'fields': (
                'prikazi_newsletter',
                'newsletter_naslov',
                'newsletter_podnaslov',
                'newsletter_placeholder',
                'newsletter_dugme',
                'newsletter_napomena',
            ),
            'description': (
                'Tamni box pored Vlog/Blog kartica. '
                'Prijave → Marketing → Pretplatnici. '
                'Blog kartice: meni Vlogovi (naslov, slika, kratki opis, datum).'
            ),
        }),
        ('④ Kontakt (header telefon + floating dugmad)', {
            'fields': (
                'kontakt_telefon',
                'kontakt_messenger',
                'kontakt_prikazi_whatsapp',
                'kontakt_prikazi_viber',
                'kontakt_prikazi_messenger',
                'kontakt_boja_whatsapp',
                'kontakt_boja_viber',
                'kontakt_boja_messenger',
            ),
            'description': 'Telefon se prikazuje u headeru. Floating WhatsApp/Viber/Messenger donje desno.',
        }),
        ('⑤ Boje dugmadi', {
            'fields': (
                'boja_dugme_korpa',
                'boja_dugme_korpa_hover',
                'boja_dugme_banner',
                'boja_dugme_banner_hover',
            ),
            'description': 'Zelena #5BB805 = mockup. Korpa na karticama + CTA na bannerima.',
        }),
        ('⑥ Dostava', {
            'fields': ('dostava_naziv', 'dostava_cijena', 'besplatna_dostava_od'),
            'description': 'Cijena dostave i prag besplatne dostave (korpa / checkout / promo tekst).',
        }),
        ('Exit popup (cijeli sajt)', {
            'fields': (
                'korpa_exit_popup_aktivan',
                'korpa_exit_popup_artikal',
                'korpa_exit_popup_popust',
            ),
            'description': (
                '„Poslednji minut” — kad kupac hoće da izađe. '
                'Artikal: 1) skoro-korpa, 2) gledanje, 3) fallback artikal. '
                'Popust %: prazno/0 = ponuda bez −% (regularna cijena); unesi % samo za sniženje.'
            ),
            'classes': ('collapse',),
        }),
        ('Registracija i nagradna igra', {
            'fields': (
                'welcome_reg_popup_aktivan',
                'welcome_reg_popust',
                'welcome_reg_delay_seconds',
                'online_nagrada_bočni_aktivan',
                'online_nagrada_delay_seconds',
            ),
            'description': (
                '1) Registracija + % na prvu narudžbu — gostu na početku. '
                '2) Nagradna igra — mali pulsirajući popup sa strane (treba aktivna kampanja Online nagrada).'
            ),
            'classes': ('collapse',),
        }),
        ('Chat sa kupcem', {
            'fields': (
                'chat_sa_kupcem_aktivan',
                'chat_delay_seconds',
                'chat_pozdrav_poruka',
                'chat_avatar_slika',
                'pregled_chat_avatara',
            ),
            'description': (
                'Proaktivni live chat: poslije delay-a iskoči kupcu. '
                'Slika (avatar) se vidi na balonu i u headeru chata. '
                'Preporuka: 256×256 px, kvadrat PNG/JPG.'
            ),
        }),
        ('Savjetnik i online posjetioci', {
            'fields': (
                'savjetnik_aktivan',
                'javno_online_posjetioci',
            ),
            'description': (
                '1) Ribolovački savjetnik — uključi/isključi chat „Savjeti pri kupovini”. '
                '2) Javni prikaz — svi na sajtu vide koliko je ljudi online (privatno: grad + gost/kupac).'
            ),
            'classes': ('collapse',),
        }),
        ('Pogodnosti', {
            'fields': (
                'novi_korisnik_besplatna_dostava',
                'novi_korisnik_popust_postotak',
                'novi_korisnik_popust_km',
            ),
            'description': 'Pogodnosti za registrovane korisnike na prvoj narudžbi. Popust u % i KM se mogu kombinovati.',
            'classes': ('collapse',),
        }),
        ('SEO — globalno (Google / društvene mreže)', {
            'fields': (
                'seo_organizacija_naziv',
                'seo_email',
                'seo_grad',
                'seo_drzava',
                'seo_facebook_url',
                'seo_instagram_url',
                'google_site_verification',
                'seo_title_suffix',
                'seo_title',
                'meta_description',
                'og_image',
            ),
            'description': (
                '<strong>Prioritet unosa (webshop SEO):</strong><br>'
                '1) <em>SEO stranica</em> meni — Početna, Akcija, Noviteti, O nama, Blog… '
                '(title, description, H1, SEO tekst)<br>'
                '2) Svaka <em>Kategorija</em> — unique title + description + tekst ispod liste<br>'
                '3) Top / novi <em>Artikli</em> — ručni title/description kad želiš precizan ranking<br>'
                '4) Ovdje: OG slika 1200×630, Search Console kod, FB/IG linkovi, naziv trgovine<br>'
                '5) Sitemap: <code>/sitemap.xml</code> · Robots: <code>/robots.txt</code><br>'
                '<em>Ne popunjavaj sve artikle ručno</em> — sistem automatski generiše title/opis; '
                'ručno samo za prioritete i kategorije.'
            ),
        }),
        ('Stranica artikla — povezani artikli', {
            'fields': ('naslov_povezani', 'podnaslov_povezani'),
            'description': 'Naslov karusela povezanih artikala. U podnaslovu koristite {kategorija} za naziv kategorije.',
            'classes': ('collapse',),
        }),
        ('Stranica artikla — badge i uslovi', {
            'fields': ('badge_product_detail', 'pregled_badgea', 'politika_dostava', 'politika_povrat', 'politika_garancija'),
            'description': (
                'Badge se prikazuje u gornjem lijevom uglu slike artikla (npr. garancija). '
                'Tekstovi ispod dugmeta „Dodaj u korpu”. Sva polja su opcionalna.'
            ),
            # NE collapse — greške na ovim poljima su bile nevidljive i blokirale Save
        }),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        messages.success(
            request,
            'Podešavanja su sačuvana. Ako mijenjaš početnu (noviteti / akcija / brendovi), '
            'osvježi početnu stranicu (hard refresh).',
        )

    @admin.display(description='Pregled loga (64px visina)')
    def pregled_loga(self, obj):
        if obj and obj.logo:
            return format_html(
                '<img src="{}" style="height:64px;max-width:480px;object-fit:contain;border:1px solid #eee;border-radius:4px;" />',
                obj.logo.url,
            )
        return 'Nema loga — tekstualni logo. Upload: originalna slika (bez ofarbavanja). Za crni header: PNG, transparentna pozadina, bijela/zelena grafika.'

    @admin.display(description='Pregled loga glavnog sajta')
    def pregled_loga_glavnog_sajta(self, obj):
        if obj and obj.logo_glavni_sajt:
            return format_html(
                '<img src="{}" style="height:28px;max-width:200px;object-fit:contain;border:1px solid #eee;border-radius:4px;" />',
                obj.logo_glavni_sajt.url,
            )
        return 'Nema loga — red „by + logo” se ne prikazuje u headeru dok ne uploadujete sliku.'

    @admin.display(description='Pregled favicona (32px)')
    def pregled_favicona(self, obj):
        if obj and obj.favicon:
            return format_html(
                '<img src="{}" style="width:32px;height:32px;object-fit:contain;border:1px solid #eee;border-radius:4px;" />',
                obj.favicon.url,
            )
        return 'Nema ikone — preglednik koristi default ikonu.'

    @admin.display(description='Pregled badgea')
    def pregled_badgea(self, obj):
        if obj and obj.badge_product_detail:
            return format_html(
                '<img src="{}" style="max-width:128px;max-height:128px;object-fit:contain;border:1px solid #eee;border-radius:4px;background:#f8f8f8;" />',
                obj.badge_product_detail.url,
            )
        return 'Nema badgea — upload PNG s transparentnom pozadinom (npr. garancija).'

    @admin.display(description='Pregled chat slike')
    def pregled_chat_avatara(self, obj):
        if obj and obj.chat_avatar_slika:
            return format_html(
                '<img src="{}" style="width:72px;height:72px;object-fit:cover;border-radius:50%;'
                'border:2px solid #0f766e;background:#f0fdfa;" />',
                obj.chat_avatar_slika.url,
            )
        return 'Nema slike — default ikona balona. Upload 256×256 px kvadrat.'


@admin.register(PageSEO)
class PageSEOAdmin(admin.ModelAdmin):
    save_on_top = True
    list_display = (
        'page_key', 'seo_title_kratko', 'h1_naslov',
        'ima_meta', 'ima_tekst_iznad', 'ima_tekst_ispod', 'azuriran',
    )
    list_filter = ('page_key',)
    search_fields = ('seo_title', 'meta_description', 'h1_naslov')
    ordering = ('page_key',)
    fieldsets = (
        ('Stranica', {
            'fields': ('page_key',),
            'description': (
                '<strong>Kako Google rangira webshop</strong><br>'
                '• <b>Početna</b> — brand + glavne ključne riječi (oprema za ribolov, BiH)<br>'
                '• <b>Akcija / Noviteti</b> — landing za te upite<br>'
                '• <b>O nama / Plaćanje / Blog</b> — trust + sadržaj (E-E-A-T)<br>'
                '• Korpa/checkout/prijava — manje bitno (noindex)<br><br>'
                '<b>Title</b>: 50–60 znakova, ključna riječ ispred, brand na kraju<br>'
                '<b>Meta description</b>: 140–160 znakova, benefit + CTA (ne ranking factor direktno, ali CTR da)<br>'
                '<b>H1</b>: jedan jasan naslov, može blago drugačiji od title-a<br>'
                '<b>SEO tekst</b>: 150–400 riječi na važnim landinzima (ne keyword stuffing)<br>'
                'Artikli / kategorije / brendovi → SEO polja na njihovim formama.'
            ),
        }),
        ('SEO title & meta description', {
            'fields': ('seo_title', 'meta_description'),
            'description': 'Ovo vidi Google u rezultatima pretrage. Unique po stranici.',
        }),
        ('H1 na stranici', {
            'fields': ('h1_naslov',),
        }),
        ('SEO tekstovi (on-page sadržaj)', {
            'fields': ('seo_tekst_iznad', 'seo_tekst_ispod'),
            'description': (
                'Tekst iznad / ispod proizvoda ili sadržaja. '
                'Najviše vrijedi na: Početna (ispod), Akcija, Noviteti, kategorije.'
            ),
        }),
    )

    @admin.display(description='SEO title')
    def seo_title_kratko(self, obj):
        t = obj.seo_title or '—'
        return t if len(t) <= 55 else t[:52] + '…'

    @admin.display(description='Meta', boolean=True)
    def ima_meta(self, obj):
        return bool(obj.meta_description)

    @admin.display(description='Iznad', boolean=True)
    def ima_tekst_iznad(self, obj):
        return bool(obj.seo_tekst_iznad)

    @admin.display(description='Ispod', boolean=True)
    def ima_tekst_ispod(self, obj):
        return bool(obj.seo_tekst_ispod)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        'naziv', 'roditelj', 'nivo_prikaz', 'search_tagovi_kratko',
        'meta_title', 'redoslijed', 'prikazi_u_meniju', 'aktivan',
    )
    list_filter = ('aktivan', 'prikazi_u_meniju', 'roditelj')
    list_editable = ('redoslijed', 'prikazi_u_meniju', 'aktivan')
    prepopulated_fields = {'slug': ('naziv',)}
    search_fields = ('naziv', 'slug', 'meta_title', 'meta_description', 'search_tagovi')
    autocomplete_fields = ('roditelj',)
    actions = ['bulk_assign_search_tags']
    fieldsets = (
        ('Osnovno', {
            'fields': ('naziv', 'slug', 'roditelj'),
            'description': 'Ostavite roditelja praznog za glavnu kategoriju u meniju (npr. Men, Women). '
                           'Za podkategoriju izaberite roditelja. Za sub-podkategoriju izaberite podkategoriju kao roditelja.',
        }),
        ('Search tagovi (samo podkategorije)', {
            'fields': ('search_tagovi',),
            'description': (
                'Samo za podkategorije (ne za glavne kategorije u meniju). '
                'Neograničen broj tagova — odvoji zarezom ili novim redom '
                '(npr. masinica, masince, rola, role, prut, štap). '
                'Masovno: označi podkategorije → akcija „Bulk dodaj tagove u podkategorije”.'
            ),
        }),
        ('Prikaz', {
            'fields': ('redoslijed', 'prikazi_u_meniju', 'aktivan'),
        }),
        ('SEO (Google) — kategorija je ključna za ranking', {
            'fields': (
                'meta_title', 'meta_description', 'h1_naslov',
                'seo_tekst_iznad', 'seo_tekst_ispod',
            ),
            'description': (
                '<strong>Prioritet #1 poslije početne.</strong> Svaka kategorija treba unique sadržaj.<br>'
                '• <b>SEO title</b> (50–60): npr. „Štapovi za šarana | Oprema za ribolov — opremazaribolov.ba”<br>'
                '• <b>Meta description</b> (140–160): što nudiš + benefit + CTA<br>'
                '• <b>H1</b>: npr. „Štapovi za šarana” (može kraće od title-a)<br>'
                '• <b>SEO tekst ispod</b>: 2–4 pasusa o kategoriji (ne copy-paste isti tekst)<br>'
                'Prazno = automatski title/opis (dobar default, ali ručno je bolje za top kategorije).'
            ),
        }),
        ('Odoo', {
            'fields': ('odoo_category_id',),
            'classes': ('collapse',),
            'description': 'ID Odoo product.category za automatsko mapiranje pri importu.',
        }),
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'search_tagovi' and formfield is not None:
            formfield.widget = forms.Textarea(attrs={
                'rows': 8,
                'cols': 80,
                'style': 'width:100%;max-width:720px;font-family:monospace;',
                'placeholder': (
                    'masinica, masince, rola\n'
                    'stap za pecanje sarana\n'
                    'prut, štap\n'
                    '(svaka linija ili zarez = tag; duga rečenica = jedan tag)'
                ),
            })
            formfield.help_text = (
                'Neograničen broj tagova. Zarez ili novi red dijele tagove. '
                'Duga rečenica (npr. „stap za pecanje sarana”) je jedan tag — '
                'search je izlistava kao tu podkategoriju.'
            )
        return formfield

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        # Sakrij search tagove za glavne kategorije (bez roditelja)
        if obj is not None and not obj.roditelj_id:
            fieldsets = [
                fs for fs in fieldsets
                if fs[0] != 'Search tagovi (samo podkategorije)'
            ]
        return fieldsets

    def save_model(self, request, obj, form, change):
        if not obj.roditelj_id:
            obj.search_tagovi = ''
        super().save_model(request, obj, form, change)

    @admin.display(description='Nivo')
    def nivo_prikaz(self, obj):
        levels = ['Glavna', 'Podkategorija', 'Sub-podkategorija']
        return levels[min(obj.nivo, 2)]

    @admin.display(description='Search tagovi')
    def search_tagovi_kratko(self, obj):
        if not obj.roditelj_id:
            return '—'
        raw = (obj.search_tagovi or '').strip()
        if not raw:
            return '—'
        if len(raw) > 48:
            return raw[:45] + '…'
        return raw

    def bulk_assign_search_tags(self, request, queryset):
        queryset = queryset.select_related('roditelj').order_by('redoslijed', 'naziv')
        subcategories = queryset.filter(roditelj__isnull=False)
        main_skipped = queryset.filter(roditelj__isnull=True).count()

        if request.method == 'POST' and 'apply' in request.POST:
            selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            count = 0
            skipped = 0
            for pk_str in selected_ids:
                try:
                    pk = int(pk_str)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                # Samo podkategorije
                cat = Category.objects.filter(pk=pk, roditelj__isnull=False).first()
                if not cat:
                    skipped += 1
                    continue
                raw = request.POST.get(f'search_tagovi_{pk}', None)
                if raw is None:
                    skipped += 1
                    continue
                normalized = Category.normalize_search_tagovi(raw)
                updated = Category.objects.filter(pk=pk, roditelj__isnull=False).update(
                    search_tagovi=normalized,
                )
                if updated:
                    count += 1
                else:
                    skipped += 1

            if count:
                self.message_user(
                    request,
                    f'Search tagovi sačuvani za {count} podkategorij(e).',
                    messages.SUCCESS,
                )
            if skipped and not count:
                self.message_user(
                    request,
                    'Nijedna podkategorija nije ažurirana. '
                    'Tagovi se odnose samo na podkategorije, ne na glavne.',
                    messages.WARNING,
                )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_category_changelist'))

        if not subcategories.exists():
            self.message_user(
                request,
                'Označi barem jednu podkategoriju. '
                'Search tagovi ne važe za glavne kategorije.',
                messages.WARNING,
            )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_category_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Bulk dodaj tagove u podkategorije',
            'queryset': subcategories,
            'main_skipped': main_skipped,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_search_tags',
        }
        return render(
            request,
            'admin/EcommerceApp/category/bulk_assign_tags.html',
            context,
        )

    bulk_assign_search_tags.short_description = 'Bulk dodaj tagove u podkategorije'


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'roditelj', 'slug')
    list_filter = ('roditelj',)
    prepopulated_fields = {'slug': ('naziv',)}
    search_fields = ('naziv', 'slug')
    autocomplete_fields = ('roditelj',)
    fieldsets = (
        (None, {
            'fields': ('naziv', 'slug', 'roditelj'),
        }),
    )


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'slug', 'meta_title', 'pregled_loga')
    prepopulated_fields = {'slug': ('naziv',)}
    search_fields = ('naziv', 'meta_title', 'meta_description')
    readonly_fields = ('pregled_loga_veliki',)
    fieldsets = (
        ('Osnovno', {
            'fields': ('naziv', 'slug', 'slika', 'pregled_loga_veliki'),
        }),
        ('SEO (Google)', {
            'fields': (
                'meta_title', 'meta_description', 'h1_naslov',
                'seo_tekst_iznad', 'seo_tekst_ispod',
            ),
            'description': 'Opcionalno. Koristi se kad se filtrira katalog po brendu.',
        }),
    )

    @admin.display(description='Logo')
    def pregled_loga(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="height:24px;max-width:100px;object-fit:contain;" />',
                obj.slika.url,
            )
        return '—'

    @admin.display(description='Pregled loga (200×48)')
    def pregled_loga_veliki(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="width:200px;height:48px;object-fit:contain;border:1px solid #eee;border-radius:4px;" />',
                obj.slika.url,
            )
        return 'Nema loga — prikazuje se naziv brenda'


class AkcijaBundleLineForm(forms.ModelForm):
    """Bundle: samo aktivni artikli na stanju."""

    class Meta:
        model = AkcijaBundleLine
        fields = '__all__'

    def clean_product(self):
        product = self.cleaned_data.get('product')
        if product and not getattr(product, 'aktivan', False):
            raise forms.ValidationError('Artikal mora biti aktivan na sajtu.')
        if product and not getattr(product, 'na_stanju', False):
            raise forms.ValidationError(
                'Ne možeš dodati artikal koji nije na stanju. '
                'Izaberi samo artikle dostupne na sajtu.'
            )
        return product


class AkcijaBundleLineInline(admin.TabularInline):
    model = AkcijaBundleLine
    form = AkcijaBundleLineForm
    extra = 2
    min_num = 0
    autocomplete_fields = ('product',)
    fields = ('product', 'quantity', 'popust_postotak', 'redoslijed')
    ordering = ('redoslijed', 'id')
    verbose_name = 'Stavka seta'
    verbose_name_plural = (
        'BUNDLE SET — samo artikli NA STANJU. '
        'Artikal + količina (+ opcionalno % po artiklu). '
        'Količina 2 = jedna slika ×2. Prazan % na liniji = % seta.'
    )
    classes = ('akcija-inline-bundle-lines',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'product':
            kwargs['queryset'] = Product.objects.filter(
                aktivan=True, na_stanju=True,
            ).order_by('naziv')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        original_clean = formset.clean

        def clean(self):
            if original_clean:
                original_clean(self)
            if any(self.errors):
                return
            parent = getattr(self, 'instance', None)
            tip = None
            if parent is not None:
                tip = getattr(parent, 'tip', None)
            # Ako je tip bundle (ili dolazi iz requesta)
            if tip != 'bundle' and request is not None:
                tip = request.POST.get('tip') or tip
            if tip != 'bundle':
                return
            total_units = 0
            for form in self.forms:
                if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                    continue
                if form.cleaned_data.get('DELETE'):
                    continue
                product = form.cleaned_data.get('product')
                if not product:
                    continue
                if not getattr(product, 'na_stanju', False) or not getattr(product, 'aktivan', False):
                    from django.core.exceptions import ValidationError
                    raise ValidationError(
                        f'„{product.naziv}” nije na stanju / nije aktivan — '
                        'u bundle možeš dodati samo dostupne artikle.'
                    )
                qty = form.cleaned_data.get('quantity') or 1
                total_units += max(1, int(qty))
            if total_units < 2:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    'Bundle set mora imati ukupno barem 2 komada. '
                    'Npr. isti artikal s količinom 2 (1+1), ili dva artikla.'
                )

        formset.clean = clean
        return formset


class AkcijaQtyTierInline(admin.TabularInline):
    model = AkcijaQtyTier
    form = AkcijaQtyTierForm
    extra = 3
    min_num = 0
    fields = ('quantity', 'popust_postotak', 'redoslijed')
    ordering = ('quantity', 'redoslijed', 'id')
    verbose_name = 'Količina + %'
    verbose_name_plural = (
        '⬇ KUPI VIŠE — ovdje unesi redove (obavezno!): '
        'količina 2 + popust %, količina 3 + popust %. '
        'Ne u „BUNDLE SET” iznad — to je za druge artikle.'
    )
    classes = ('akcija-inline-qty-tiers',)

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        original_clean = formset.clean

        def clean(self):
            if original_clean:
                original_clean(self)
            parent = getattr(self, 'instance', None)
            tip = None
            if parent is not None:
                tip = getattr(parent, 'tip', None)
            if tip != 'qty_deal' and request is not None:
                tip = request.POST.get('tip') or tip
            if tip != 'qty_deal':
                return
            # Ako pojedinačni redovi imaju greške — poruka i dalje odozgo
            if any(self.errors):
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    'Ispravi greške u redovima ispod (količina ≥ 2, popust npr. 10 ili 10,5).'
                )
            tiers = 0
            seen_qty = set()
            for form in self.forms:
                if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                    continue
                if form.cleaned_data.get('DELETE'):
                    continue
                qty = form.cleaned_data.get('quantity')
                pct = form.cleaned_data.get('popust_postotak')
                if qty in (None, '') or pct in (None, ''):
                    continue
                q = int(qty)
                if q in seen_qty:
                    from django.core.exceptions import ValidationError
                    raise ValidationError(
                        f'Količina {q} je unesena više puta — svaka količina samo jednom.'
                    )
                seen_qty.add(q)
                tiers += 1
            if tiers < 1:
                from django.core.exceptions import ValidationError
                raise ValidationError(
                    'Za „Kupi više” morate unijeti barem jedan red ispod: '
                    'npr. Kupi 2 komada + Popust 10. '
                    '(Ne u BUNDLE SET — tamo se unosi set različitih artikala.)'
                )

        formset.clean = clean
        return formset


class ProductDwellItemInline(admin.TabularInline):
    """Po artiklu unesi svoj flash popust % — samo artikli na stanju."""
    model = ProductDwellItem
    fk_name = 'settings'
    extra = 1
    autocomplete_fields = ('product',)
    fields = ('product', 'popust')
    verbose_name = 'Artikal (AI dwell)'
    verbose_name_plural = (
        'AI dwell artikli — popust %: 0 = bez popusta; npr. 8, 12, 20 = snizenje'
    )
    classes = ('akcija-inline-dwell-items',)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'product':
            from .models import Product
            kwargs['queryset'] = (
                Product.objects
                .filter(aktivan=True, na_stanju=True)
                .order_by('naziv')
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ProductDwellItemAkcijaInline(ProductDwellItemInline):
    """
    ProductDwellItem je FK na SiteSettings, ne na Akciju.
    Inicijalizira se s parent_model=SiteSettings; formset uvijek veže SiteSettings.load().
    """

    def get_formset(self, request, obj=None, **kwargs):
        from .models import SiteSettings

        settings_obj = SiteSettings.load()
        FormSet = super().get_formset(request, settings_obj, **kwargs)

        class _DwellFormSet(FormSet):
            def __init__(self, *args, **kwargs):
                kwargs['instance'] = SiteSettings.load()
                super().__init__(*args, **kwargs)

        return _DwellFormSet


@admin.register(AIProdajaSettings)
class AIProdajaSettingsAdmin(admin.ModelAdmin):
    """Zastarjeli proxy — sakriven; sve se uređuje u Akcije → tip AI."""
    inlines = [ProductDwellItemInline]

    def get_model_perms(self, request):
        return {}

    def has_module_permission(self, request):
        return False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ─── Ribolovački savjetnik: početnički setovi ───────────────────────

class AdvisorBeginnerSetInline(admin.TabularInline):
    """Na tipu seta — brzo dodaj setove (artikle uredi u Set adminu)."""
    model = AdvisorBeginnerSet
    extra = 1
    fields = ('naziv', 'emoji', 'popust_postotak', 'redoslijed', 'aktivan')
    show_change_link = True
    ordering = ('redoslijed', 'id')
    verbose_name = 'Set'
    verbose_name_plural = (
        'Setovi za ovaj tip — klikni na set da dodaš artikle'
    )


class AdvisorBeginnerSetItemForm(forms.ModelForm):
    """Validacija: samo artikli na stanju."""

    class Meta:
        model = AdvisorBeginnerSetItem
        fields = '__all__'

    def clean_product(self):
        product = self.cleaned_data.get('product')
        if product and not product.na_stanju:
            raise forms.ValidationError('Možeš dodati samo artikle koji su na stanju.')
        if product and not product.aktivan:
            raise forms.ValidationError('Artikal mora biti aktivan.')
        return product


class AdvisorBeginnerSetItemInline(admin.TabularInline):
    model = AdvisorBeginnerSetItem
    form = AdvisorBeginnerSetItemForm
    extra = 2
    # Unos slovo po slovo (autocomplete) — filtrirano u ProductAdmin.get_search_results
    autocomplete_fields = ('product',)
    fields = ('product', 'kolicina', 'redoslijed', 'linija_cijena')
    readonly_fields = ('linija_cijena',)
    ordering = ('redoslijed', 'id')
    verbose_name = 'Artikal u setu'
    verbose_name_plural = 'Artikli u setu — kucaj naziv (samo na stanju)'

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'product':
            kwargs['queryset'] = Product.objects.filter(
                aktivan=True, na_stanju=True,
            ).order_by('naziv')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='Iznos')
    def linija_cijena(self, obj):
        if not obj or not obj.pk or not obj.product_id:
            return '—'
        try:
            iznos = obj.linija_iznos()
            return f'{iznos} KM'
        except Exception:
            return '—'


@admin.register(AdvisorBeginnerFishType)
class AdvisorBeginnerFishTypeAdmin(admin.ModelAdmin):
    list_display = (
        'naziv', 'emoji', 'code', 'setovi_aktivni', 'redoslijed', 'aktivan',
    )
    list_editable = ('redoslijed', 'aktivan')
    list_filter = ('aktivan',)
    search_fields = ('naziv', 'code')
    prepopulated_fields = {'code': ('naziv',)}
    inlines = [AdvisorBeginnerSetInline]
    ordering = ('redoslijed', 'naziv')

    fieldsets = (
        (None, {
            'fields': ('naziv', 'code', 'emoji', 'redoslijed', 'aktivan'),
            'description': (
                'Tipovi setova koje nudi savjetnik (npr. Saranski set, Feeder set, '
                'Pečaljke za plovak). Za varaličarski dodaj tipove s kodovima: '
                'stuka, som, ul — u chatu se grupišu pod „Varaličarski set”. '
                'Dodaj setove ispod, pa u svaki set artikle. '
                'Prikazuju se samo aktivni setovi s artiklima na stanju.'
            ),
        }),
    )

    @admin.display(description='Aktivni setovi')
    def setovi_aktivni(self, obj):
        if not obj or not obj.pk:
            return 0
        return obj.setovi.filter(aktivan=True).count()


@admin.register(AdvisorBeginnerSet)
class AdvisorBeginnerSetAdmin(admin.ModelAdmin):
    list_display = (
        'naziv', 'fish_type', 'popust_postotak', 'broj_artikala',
        'iznos_regularni', 'iznos_snizeni', 'redoslijed', 'aktivan',
    )
    list_filter = ('fish_type', 'aktivan')
    list_editable = ('redoslijed', 'aktivan')
    search_fields = ('naziv', 'fish_type__naziv')
    autocomplete_fields = ()
    inlines = [AdvisorBeginnerSetItemInline]
    ordering = ('fish_type__redoslijed', 'redoslijed', 'id')

    fieldsets = (
        (None, {
            'fields': (
                'fish_type', 'naziv', 'emoji', 'popust_postotak',
                'redoslijed', 'aktivan', 'popis',
            ),
            'description': (
                'Dodaj artikle u tabeli ispod. '
                'Iznos se sabira automatski. Popust % je opcionalan na cijeli set.'
            ),
        }),
        ('Pregled cijene', {
            'fields': ('iznos_regularni', 'iznos_snizeni'),
        }),
    )
    readonly_fields = ('iznos_regularni', 'iznos_snizeni')

    @admin.display(description='Artikala')
    def broj_artikala(self, obj):
        if not obj or not obj.pk:
            return 0
        return obj.stavke.count()

    @admin.display(description='Regularno')
    def iznos_regularni(self, obj):
        if not obj or not obj.pk:
            return '—'
        return f'{obj.regularni_iznos()} KM'

    @admin.display(description='Sa popustom')
    def iznos_snizeni(self, obj):
        if not obj or not obj.pk:
            return '—'
        reg = obj.regularni_iznos()
        sale = obj.snizeni_iznos()
        if obj.ima_popust():
            return format_html(
                '<strong style="color:#0a0">{} KM</strong> '
                '<span style="text-decoration:line-through;color:#888">{} KM</span> '
                '(-{}%)',
                sale, reg, obj.popust_postotak,
            )
        return f'{sale} KM'


@admin.register(Akcija)
class AkcijaAdmin(admin.ModelAdmin):
    form = AkcijaAdminForm
    list_display = (
        'naziv', 'tip', 'artikal', 'gratis_artikal', 'popust_postotak',
        'bundle_trigger', 'aktivan', 'redoslijed',
    )
    list_filter = ('tip', 'aktivan', 'bundle_trigger')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = (
        'naziv', 'artikal__naziv', 'gratis_artikal__naziv', 'kategorija__naziv',
    )
    autocomplete_fields = ('artikal', 'gratis_artikal', 'kategorija')
    filter_horizontal = ('bundle_artikli',)
    # Bundle inline; AI dwell artikli se dodaju u get_inline_instances
    # (ProductDwellItem.FK je SiteSettings, ne Akcija — nije u inlines zbog admin.E202)
    inlines = [AkcijaBundleLineInline]

    class Media:
        js = ('admin/js/akcija_admin.js',)
        css = {'all': ('admin/css/ozr_admin.css',)}

    # --- Fieldseti: svako polje SAMO JEDNOM (admin.E012) ---
    # JS pri promjeni tipa prikaže/sakrije relevantne sekcije.
    _FS_BASE = (
        (None, {
            'fields': ('naziv', 'tip', 'aktivan', 'redoslijed'),
            'description': (
                'Odaberi tip akcije — prikazuju se samo polja za taj tip '
                '(bundle / kupi više / + ponuda / AI dwell).'
            ),
        }),
    )
    _FS_ARTIKLI = (
        ('Artikli i popust (možeš mijenjati — nije potrebno brisati akciju)', {
            'fields': (
                'artikal',
                'popust_postotak',
                'gratis_artikal',
            ),
            'description': (
                'Artikle možeš u bilo kojem trenutku promijeniti: pretraži, odaberi, Sačuvaj. '
                'Ne moraš brisati akciju i kreirati novu. '
                '—— + Ponuda: '
                '1) Trigger = artikal koji kupac doda u korpu → iskače popup; '
                '2) Popust % (opcionalno) na drugi artikal; prazno = redovna cijena; '
                '3) Ponuda artikal = što se nudi u popupu. '
                '—— Kupi više: Trigger artikal; modal pri dodavanju u korpu (samo 2+ kom). '
                '—— Bundle: % seta; trigger artikal samo ako je trigger „odabrani artikal”.'
            ),
        }),
    )
    _FS_BUNDLE_EXTRA = (
        ('Pop-up bundle — dodatno', {
            'fields': (
                'bundle_trigger',
                'kategorija',
                'popup_delay_seconds',
            ),
            'description': 'Šta trigeruje set i kašnjenje popupa.',
        }),
        ('Legacy M2M (opcionalno)', {
            'classes': ('collapse',),
            'fields': ('bundle_artikli',),
            'description': 'Stari način (bez količine). Preferiraj inline stavke.',
        }),
    )
    _FS_QTY = (
        ('Kupi više — količina i popust', {
            'fields': (
                'qty_2_popust',
                'qty_3_popust',
                'qty_4_popust',
                'qty_5_popust',
                'qty_6_popust',
            ),
            'description': (
                'Modal iskače tek kad kupac doda artikal u korpu (ne page popup). '
                'Nudi se samo 2+ kom s % — nema opcije „1 kom”. '
                'Ako odbije (X / Ne, hvala) — u korpu ide količina koju je unio. '
                'Npr. 10 pored „Kupi 2 komada” (= -10% za 2 kom). Prazno = opcija se ne nudi.'
            ),
        }),
    )
    _FS_PRIKAZ = (
        ('Prikaz i publika', {
            'fields': (
                'tekst_dugmeta',
                'boja_dugmeta',
                'boja_opisa',
                'za_prijavljene',
                'za_neprijavljene',
                'ponovo_poslije_dana',
            ),
            'description': 'Za bundle i „Kupi više”. + Ponuda ovo ne koristi.',
        }),
    )
    _FS_AI = (
        ('AI prodaja — automatski popup (prati kupca)', {
            'fields': (
                'browse_interest_popup_aktivan',
                'browse_interest_popust',
            ),
            'description': (
                'Uključi/isključi automatski popup koji prati kupca '
                '(gledanje, skoro-korpa) i iskače s 1–2 artikla. '
                'Do 2 ponude po posjeti, razmak ~3 min. '
                'Popust %: unesi 0 za ponudu BEZ sniženja (samo preporuka); '
                'npr. 5–15 za popust. Max 50%.'
            ),
        }),
        ('AI dwell — uključeno / popust / tajmer', {
            'fields': (
                'product_dwell_popup_aktivan',
                'product_dwell_popust',
                'product_dwell_flash_seconds',
                'product_dwell_sale_pulse',
            ),
            'description': (
                'Flash cijena na product page (ne popup). '
                'Artikle dodaj u tabeli „AI dwell artikli”. '
                'Popust %: 0 = bez −% / precrtane cijene; '
                'npr. 8–15 = flash snizenje.'
            ),
        }),
        ('AI dwell — tekstovi', {
            'fields': (
                'product_dwell_tag_text',
                'product_dwell_timer_label',
                'product_dwell_catalog_label',
            ),
        }),
        ('AI dwell — boje (product page)', {
            'fields': (
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
            ),
        }),
        ('AI dwell — boje (katalog / pretraga)', {
            'fields': (
                'product_dwell_boja_kartica_bg',
                'product_dwell_boja_kartica_bg2',
                'product_dwell_boja_kartica_border',
                'product_dwell_boja_kartica_stara',
                'product_dwell_boja_kartica_nova',
                'product_dwell_boja_kartica_badge_bg',
                'product_dwell_boja_kartica_badge_tekst',
                'product_dwell_boja_kartica_label',
            ),
        }),
    )

    # Svako polje tačno jednom — Django admin.E012
    fieldsets = (
        _FS_BASE
        + _FS_ARTIKLI
        + _FS_BUNDLE_EXTRA
        + _FS_QTY
        + _FS_PRIKAZ
        + _FS_AI
    )

    def _resolve_akcija_tip(self, request, obj=None):
        if request is not None and request.method == 'POST':
            tip = (request.POST.get('tip') or '').strip()
            if tip:
                return tip
        if obj is not None and getattr(obj, 'tip', None):
            return obj.tip
        return None

    def get_fieldsets(self, request, obj=None):
        """
        Samo polja za odabrani tip — i na GET i na POST.
        (Stari POST=sva polja uzrokovao „Please correct the errors below”
        jer forma nije imala skrivena required polja u HTML-u.)
        """
        tip = self._resolve_akcija_tip(request, obj)
        if tip == Akcija.Tip.PONUDA:
            return self._FS_BASE + self._FS_ARTIKLI
        if tip == Akcija.Tip.BUNDLE:
            return self._FS_BASE + self._FS_ARTIKLI + self._FS_BUNDLE_EXTRA + self._FS_PRIKAZ
        if tip == Akcija.Tip.QTY_DEAL:
            return self._FS_BASE + self._FS_ARTIKLI + self._FS_QTY + self._FS_PRIKAZ
        if tip == Akcija.Tip.AI_PRODAJA:
            return self._FS_BASE + self._FS_AI
        # Novi red / nepoznat tip: svi fieldseti + JS filter
        return self.fieldsets

    def get_inline_instances(self, request, obj=None):
        from .models import SiteSettings

        tip = self._resolve_akcija_tip(request, obj)
        dwell = ProductDwellItemAkcijaInline(SiteSettings, self.admin_site)
        bundle = AkcijaBundleLineInline(self.model, self.admin_site)

        # Novi red: oba u DOM-u (JS sakrije po tipu)
        if obj is None and tip is None:
            return [bundle, dwell]
        if tip == Akcija.Tip.BUNDLE:
            return [bundle]
        if tip == Akcija.Tip.AI_PRODAJA:
            # Obavezno: tabela „AI dwell artikli” za unos popusta po artiklu
            return [dwell]
        # + Ponuda / Kupi više: bez inline tabela
        return []

    def _ensure_ai_prodaja_akcija(self):
        """Jedan red u listi Akcije za AI prodaja / AI dwell."""
        from .models import SiteSettings

        s = SiteSettings.load()
        dwell_on = bool(getattr(s, 'product_dwell_popup_aktivan', False))
        browse_on = bool(getattr(s, 'browse_interest_popup_aktivan', False))
        aktivan = dwell_on or browse_on
        obj, _created = Akcija.objects.get_or_create(
            tip=Akcija.Tip.AI_PRODAJA,
            defaults={
                'naziv': 'AI prodaja / AI dwell',
                'aktivan': aktivan,
                'redoslijed': 99,
                'tekst_dugmeta': 'AI',
            },
        )
        updates = {}
        if obj.naziv != 'AI prodaja / AI dwell':
            updates['naziv'] = 'AI prodaja / AI dwell'
        if obj.aktivan != aktivan:
            updates['aktivan'] = aktivan
        if updates:
            Akcija.objects.filter(pk=obj.pk).update(**updates)
        return obj

    def get_queryset(self, request):
        self._ensure_ai_prodaja_akcija()
        qs = super().get_queryset(request)
        return qs.filter(tip__in=Akcija.ACTIVE_TIPS)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == 'tip':
            kwargs['choices'] = [
                (Akcija.Tip.BUNDLE, Akcija.Tip.BUNDLE.label),
                (Akcija.Tip.QTY_DEAL, Akcija.Tip.QTY_DEAL.label),
                (Akcija.Tip.PONUDA, Akcija.Tip.PONUDA.label),
                (Akcija.Tip.AI_PRODAJA, Akcija.Tip.AI_PRODAJA.label),
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Dozvoli izmjenu artikala na postojećoj akciji (uključi i trenutni odabir)."""
        if db_field.name in ('artikal', 'gratis_artikal'):
            from .models import Product
            qs = Product.objects.filter(aktivan=True).order_by('naziv')
            # Object id iz URL-a change forme
            obj_id = None
            if hasattr(request, 'resolver_match') and request.resolver_match:
                obj_id = (request.resolver_match.kwargs or {}).get('object_id')
            if obj_id:
                try:
                    existing = Akcija.objects.filter(pk=obj_id).only(
                        'artikal_id', 'gratis_artikal_id',
                    ).first()
                except Exception:
                    existing = None
                if existing:
                    keep_ids = [
                        i for i in (
                            existing.artikal_id if db_field.name == 'artikal' else None,
                            existing.gratis_artikal_id if db_field.name == 'gratis_artikal' else None,
                        ) if i
                    ]
                    if keep_ids:
                        from django.db.models import Q
                        qs = Product.objects.filter(
                            Q(aktivan=True) | Q(pk__in=keep_ids),
                        ).order_by('naziv')
            kwargs['queryset'] = qs
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        # Nikad ne zaključavaj artikal / ponudu — uvijek izmjenjivo
        ro = list(super().get_readonly_fields(request, obj))
        for locked in ('artikal', 'gratis_artikal', 'popust_postotak', 'tip'):
            if locked in ro:
                ro.remove(locked)
        return ro

    def get_form(self, request, obj=None, change=False, **kwargs):
        """
        AI polja nisu na modelu Akcija — moraju biti u form.declared_fields
        (vidi AkcijaAdminForm). Ovdje osiguravamo da factory ne pukne.
        """
        from django.contrib.admin.utils import flatten_fieldsets
        from .forms import AI_SETTINGS_FIELD_NAMES, AkcijaAdminForm

        # Ako bi se base_fields izgubili (reload), ponovo zakači
        for name in AI_SETTINGS_FIELD_NAMES:
            if name not in AkcijaAdminForm.declared_fields:
                try:
                    from .forms import _make_ai_settings_formfield
                    ff = _make_ai_settings_formfield(name)
                    AkcijaAdminForm.base_fields[name] = ff
                    AkcijaAdminForm.declared_fields[name] = ff
                except Exception:
                    pass

        if 'fields' not in kwargs:
            # Uključi model + deklarisana form polja (qty + AI)
            fieldset_fields = list(flatten_fieldsets(self.get_fieldsets(request, obj)))
            kwargs['fields'] = fieldset_fields

        return super().get_form(request, obj, change=change, **kwargs)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.tip == Akcija.Tip.AI_PRODAJA:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if obj.tip not in Akcija.ACTIVE_TIPS:
            obj.tip = Akcija.Tip.BUNDLE
        # Samo jedan AI red — ne dupliciraj
        if obj.tip == Akcija.Tip.AI_PRODAJA:
            existing = (
                Akcija.objects
                .filter(tip=Akcija.Tip.AI_PRODAJA)
                .exclude(pk=obj.pk)
                .first()
            )
            if existing:
                from django.contrib import messages as django_messages
                django_messages.warning(
                    request,
                    'AI prodaja / AI dwell već postoji — uredi postojeći red u listi.',
                )
                # Preusmjeri snimanje na postojeći red
                obj.pk = existing.pk
                obj.id = existing.pk
            obj.naziv = 'AI prodaja / AI dwell'
            if hasattr(form, 'save_ai_settings'):
                site = form.save_ai_settings()
                if site is not None:
                    obj.aktivan = bool(
                        getattr(site, 'product_dwell_popup_aktivan', False)
                        or getattr(site, 'browse_interest_popup_aktivan', False)
                    )
        super().save_model(request, obj, form, change)
        if obj.tip == Akcija.Tip.AI_PRODAJA:
            return
        if hasattr(form, 'save_qty_deal_tiers'):
            form.save_qty_deal_tiers(obj)
        # Potvrda izmjene / kreiranja artikala (nije potrebno brisanje i nova akcija)
        if obj.tip in (Akcija.Tip.PONUDA, Akcija.Tip.QTY_DEAL, Akcija.Tip.BUNDLE):
            from django.contrib import messages as django_messages
            if obj.tip == Akcija.Tip.PONUDA:
                t = obj.artikal.naziv if obj.artikal_id else '—'
                g = obj.gratis_artikal.naziv if obj.gratis_artikal_id else '—'
                pct = f'{obj.popust_postotak}%' if obj.popust_postotak is not None else 'bez %'
                verb = 'ažurirana' if change else 'kreirana'
                django_messages.success(
                    request,
                    f'+ Ponuda {verb}: trigger „{t}” → ponuda „{g}” ({pct}). '
                    f'Kad kupac doda trigger u korpu, iskače DA/NE s popustom na ponudu. '
                    f'Artikle i % možeš mijenjati ovdje bez brisanja akcije.',
                )
                self._warn_ponuda_stock(request, obj)
            elif obj.tip == Akcija.Tip.QTY_DEAL and obj.artikal_id:
                django_messages.success(
                    request,
                    f'Kupi više sačuvano za „{obj.artikal.naziv}”. '
                    f'Artikal i % opcije možeš mijenjati bez brisanja akcije.',
                )

    def save_related(self, request, form, formsets, change):
        obj = form.instance
        if obj.tip == Akcija.Tip.AI_PRODAJA:
            # Dwell inline: parent je već SiteSettings u formsetu
            for formset in formsets:
                self.save_formset(request, form, formset, change=change)
            if hasattr(form, 'save_ai_settings'):
                form.save_ai_settings()
            return
        super().save_related(request, form, formsets, change)
        if hasattr(form, 'save_qty_deal_tiers'):
            form.save_qty_deal_tiers(obj)
        if obj.tip == Akcija.Tip.BUNDLE:
            if obj.bundle_unit_count() < 2:
                from django.contrib import messages as django_messages
                django_messages.warning(
                    request,
                    'Bundle set mora imati ukupno barem 2 komada '
                    '(npr. jedan artikal ×2, ili dva različita ×1).',
                )
        elif obj.tip == Akcija.Tip.QTY_DEAL:
            if not obj.qty_deal_tiers():
                from django.contrib import messages as django_messages
                django_messages.warning(
                    request,
                    '„Kupi više” treba barem jedan popust (npr. 2 kom → 10%).',
                )
        elif obj.tip == Akcija.Tip.PONUDA:
            if not obj.artikal_id or not obj.gratis_artikal_id:
                from django.contrib import messages as django_messages
                django_messages.warning(
                    request,
                    '+ Ponuda treba trigger artikal i ponuda artikal.',
                )

    def _product_offerable(self, product):
        """True ako se artikal može ponuditi u + Ponuda popup-u (na stanju)."""
        if not product or not product.aktivan:
            return False
        if product.varijacije.exists():
            return product.varijacije.filter(na_stanju=True).exists()
        return bool(product.na_stanju)

    def _warn_ponuda_stock(self, request, obj):
        from django.contrib import messages as django_messages
        if not obj or obj.tip != Akcija.Tip.PONUDA:
            return
        if obj.gratis_artikal_id and not self._product_offerable(obj.gratis_artikal):
            django_messages.warning(
                request,
                f'Ponuda artikal „{obj.gratis_artikal.naziv}” nije na stanju — '
                f'popup se NEĆE prikazati dok ne staviš artikal na stanje ili ne odabereš drugi. '
                f'Možeš ga zamijeniti ispod (Sačuvaj) bez brisanja akcije.',
            )
        if obj.artikal_id and not obj.artikal.aktivan:
            django_messages.warning(
                request,
                f'Trigger artikal „{obj.artikal.naziv}” nije aktivan — '
                f'zamijeni ga ili aktiviraj artikal.',
            )

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id)
        if obj is not None and obj.tip == Akcija.Tip.AI_PRODAJA:
            extra_context['title'] = 'AI prodaja / AI dwell'
            extra_context['show_save_and_add_another'] = False
        elif obj is not None and obj.tip == Akcija.Tip.PONUDA:
            extra_context['title'] = (
                '+ Ponuda — uredi trigger / % / ponudu (bez brisanja)'
            )
            # Upozorenje na učitavanju change forme
            if request.method == 'GET':
                self._warn_ponuda_stock(request, obj)
        elif obj is not None and obj.tip == Akcija.Tip.QTY_DEAL:
            extra_context['title'] = 'Kupi više — uredi artikal i % (bez brisanja)'
        return super().change_view(
            request, object_id, form_url, extra_context=extra_context,
        )


@admin.register(UpsellOffer)
class UpsellOfferAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'prikaz', 'get_trigger_display', 'get_deal_display', 'popust_postotak', 'aktivan', 'redoslijed')
    list_filter = ('aktivan', 'prikaz')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naziv',)
    filter_horizontal = ('ponuda_artikli',)
    autocomplete_fields = ('trigger_artikal', 'trigger_kategorija', 'deal_artikal')
    fieldsets = (
        ('Prikaz i ponuda', {
            'fields': (
                'prikaz',
                'ponuda_artikli',
                'baner_slika',
                'tekst_dugmeta',
                'popust_postotak',
                'popust_km',
            ),
            'description': (
                'Sva polja su opcionalna za klasične upsell ponude (popup/baner).'
            ),
        }),
        ('X+1 deal (zastarjelo)', {
            'fields': ('deal_artikal', 'deal_vrsta', 'deal_popust'),
            'description': 'Koristite meni Akcije → X+1 prodaja umjesto ovog polja.',
            'classes': ('collapse',),
        }),
        ('Tekstovi i trigger (opcionalno)', {
            'fields': ('naslov_ponude', 'opis_ponude', 'trigger_artikal', 'trigger_kategorija'),
            'description': (
                'Naslov/opis za popup ili checkout (npr. „Poslednja šansa”). '
                'Trigger samo za popup.'
            ),
            'classes': ('collapse',),
        }),
        ('Ostalo (opcionalno)', {
            'fields': ('naziv', 'aktivan', 'redoslijed'),
        }),
    )

    def get_trigger_display(self, obj):
        return obj.get_trigger_display()
    get_trigger_display.short_description = 'Trigger'

    def get_deal_display(self, obj):
        if obj.deal_artikal and obj.deal_vrsta:
            pct = f"{obj.deal_popust}%" if obj.deal_popust is not None else ""
            return f"{obj.deal_artikal.naziv} — {obj.deal_vrsta} ({pct})"
        return "—"
    get_deal_display.short_description = 'X+1 Deal'


@admin.register(HomeVlog)
class HomeVlogAdmin(admin.ModelAdmin):
    list_display = ('naslov', 'objavljeno', 'aktivan', 'redoslijed', 'pregled_slike')
    list_filter = ('aktivan',)
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naslov', 'slug', 'sadrzaj', 'kratki_opis')
    prepopulated_fields = {'slug': ('naslov',)}
    readonly_fields = ('pregled_slike_velika', 'kreirano')
    ordering = ('redoslijed', '-id')
    fieldsets = (
        ('Početna — Vlog / Blog kartica', {
            'fields': (
                'naslov', 'kratki_opis', 'objavljeno',
                'slika', 'pregled_slike_velika', 'sadrzaj',
            ),
            'description': (
                'Prikaz na početnoj (sekcija Vlog/Blog + newsletter). '
                'Prva 3 aktivna po redoslijedu. '
                'Naslov sekcije: Podešavanja → „naslov_blog”. '
                'Upload slike: AVIF optimizacija.'
            ),
        }),
        ('Redoslijed i status', {
            'fields': ('slug', 'redoslijed', 'aktivan', 'kreirano'),
        }),
    )

    @admin.display(description='Slika')
    def pregled_slike(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="height:40px;border-radius:4px;" />',
                obj.slika.url,
            )
        return '—'

    @admin.display(description='Pregled slike')
    def pregled_slike_velika(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="max-height:200px;border-radius:8px;" />',
                obj.slika.url,
            )
        return 'Nema slike'


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    form = BannerAdminForm
    list_display = ('naslov', 'tip', 'kategorija', 'filter_cijena_do', 'filter_cijena_od', 'aktivan', 'redoslijed', 'pregled_slike')
    list_filter = ('tip', 'aktivan')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naslov', 'podnaslov')
    autocomplete_fields = ('kategorija',)
    readonly_fields = ('pregled_slike_velika', 'pregled_slike_mobilne', 'pregled_videa')
    fieldsets = (
        ('Sadržaj', {
            'fields': (
                'naslov', 'podnaslov',
                'slika', 'pregled_slike_velika',
                'slika_mobilna', 'pregled_slike_mobilne',
                'video', 'pregled_videa',
            ),
            'description': (
                'Klik na banner vodi na kategoriju ili link (ako su postavljeni). '
                'Obavezna je slika (desktop) ili video.\n'
                '• Desktop hero: 1920×640 px (3:1)\n'
                '• Mobilni hero: 1080×1350 px (4:5) — prikaz SAMO na telefonu (≤768px). '
                'Ako nije uploadano, mobitel koristi desktop sliku.\n'
                'Video: MP4/WebM/MOV, max 6 s. Tip „Hero Carousel” za karusel.'
            ),
        }),
        ('Odredište i filter', {
            'fields': (
                'kategorija', 'link', 'filter_cijena_do', 'filter_cijena_od',
                'tekst_dugmeta', 'sekundarno_dugme', 'sekundarni_link',
            ),
            'description': (
                'Link nije obavezan — možete samo odabrati kategoriju. '
                'Do cijene 50 = artikli ≤ 50 KM; od cijene 50 = artikli ≥ 50 KM. '
                'Primjer: kategorija Mašinice + do 50 = sve mašinice ispod 50 KM.'
            ),
        }),
        ('Podešavanja', {
            'fields': ('tip', 'siroka_kartica', 'redoslijed', 'aktivan'),
        }),
    )

    @admin.display(description='Slika')
    def pregled_slike(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="height:40px;border-radius:4px;" />',
                obj.slika.url,
            )
        return '—'

    @admin.display(description='Pregled desktop slike')
    def pregled_slike_velika(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="max-height:200px;border-radius:8px;" />',
                obj.slika.url,
            )
        return 'Nema desktop slike'

    @admin.display(description='Pregled mobilne slike')
    def pregled_slike_mobilne(self, obj):
        if obj and obj.slika_mobilna:
            return format_html(
                '<img src="{}" style="max-height:220px;border-radius:8px;" />',
                obj.slika_mobilna.url,
            )
        return 'Nema mobilne slike — na telefonu se koristi desktop'

    @admin.display(description='Pregled videa')
    def pregled_videa(self, obj):
        if obj and obj.video:
            return format_html(
                '<video src="{}" style="max-height:200px;border-radius:8px;" controls muted playsinline></video>',
                obj.video.url,
            )
        return 'Nema videa'


class IstaMtSifraUNazivuFilter(admin.SimpleListFilter):
    title = 'Ista MT šifra u nazivu'
    parameter_name = 'ista_mt_sifra'

    def lookups(self, request, model_admin):
        return (
            ('da', 'Da — isti MT+broj u nazivu'),
        )

    def value(self):
        val = super().value()
        if isinstance(val, (list, tuple)):
            return val[0] if val else None
        return val

    def queryset(self, request, queryset):
        if self.value() != 'da':
            return queryset
        from django.db.models import Case, IntegerField, When

        from .product_options import duplicate_mt_name_groups

        groups = duplicate_mt_name_groups()
        ordered_ids = []
        seen = set()
        for code in sorted(groups):
            members = sorted(groups[code], key=lambda p: ((p.naziv or '').casefold(), p.pk))
            for product in members:
                if product.pk not in seen:
                    seen.add(product.pk)
                    ordered_ids.append(product.pk)
        if not ordered_ids:
            return queryset.none()
        preserved = Case(
            *[When(pk=pk, then=index) for index, pk in enumerate(ordered_ids)],
            output_field=IntegerField(),
        )
        return (
            queryset.filter(pk__in=ordered_ids)
            .annotate(_mt_dup_ord=preserved)
            .order_by('_mt_dup_ord')
        )


class ImaVarijacijeFilter(admin.SimpleListFilter):
    title = 'Varijacije'
    parameter_name = 'ima_varijacije'

    def lookups(self, request, model_admin):
        return (
            ('da', 'Ima varijacije'),
            ('ne', 'Bez varijacija'),
        )

    def queryset(self, request, queryset):
        if 'varijacije_broj' not in queryset.query.annotations:
            queryset = queryset.annotate(varijacije_broj=Count('varijacije', distinct=True))
        if self.value() == 'da':
            return queryset.filter(varijacije_broj__gt=0)
        if self.value() == 'ne':
            return queryset.filter(varijacije_broj=0)
        return queryset


class NaStanjuFilter(admin.SimpleListFilter):
    """Custom filter for 'na_stanju' that defaults to 'Yes' (in stock) selected."""
    title = 'Na stanju'
    parameter_name = 'na_stanju'

    def __init__(self, request, params, model, model_admin):
        raw = params.get('ista_mt_sifra')
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        self._show_all_for_mt = raw == 'da'
        super().__init__(request, params, model, model_admin)

    def lookups(self, request, model_admin):
        return (
            ('1', 'Da'),
            ('0', 'Ne'),
        )

    def value(self):
        val = super().value()
        if isinstance(val, (list, tuple)):
            val = val[0] if val else None
        if val is None and getattr(self, '_show_all_for_mt', False):
            return None
        if val is None:
            return '1'
        return val

    def queryset(self, request, queryset):
        val = self.value()
        if val == '1':
            return queryset.filter(na_stanju=True)
        if val == '0':
            return queryset.filter(na_stanju=False)
        return queryset

    def choices(self, changelist):
        # All option (removes the filter param)
        yield {
            'selected': self.value() not in ('0', '1'),
            'query_string': changelist.get_query_string({}, [self.parameter_name]),
            'display': 'Sve',
        }
        for lookup, title in self.lookup_choices:
            yield {
                'selected': self.value() == lookup,
                'query_string': changelist.get_query_string({self.parameter_name: lookup}),
                'display': title,
            }


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    change_list_template = 'admin/EcommerceApp/product/change_list.html'
    change_form_template = 'admin/EcommerceApp/product/change_form.html'
    actions = [
        'bulk_assign_category', 'bulk_assign_brand', 'bulk_assign_tags',
        'bulk_assign_pakovanje',
        'bulk_rename_product_images',
        'bulk_proizvedeno_u_japanu', 'bulk_ukloni_japan', 'bulk_merge_products',
        'bulk_split_variations',
        'bulk_objavi_na_olx_pik',
    ]
    filter_horizontal = ('tagovi',)
    list_display = (
        'naziv', 'varijacije_broj', 'sifra', 'mt_sifra_u_nazivu', 'brend', 'kategorija', 'cijena',
        'pakovanje_komada',
        'akcijska_cijena', 'na_stanju', 'prikazi_na_pocetnoj', 'je_novitet', 'je_hit',
        'prioritet_lagera', 'proizvedeno_u_japanu',
        'aktivan', 'datum_dodavanja', 'olx_status', 'pregled_slike',
    )
    list_filter = (
        'aktivan', NaStanjuFilter, ImaVarijacijeFilter, IstaMtSifraUNazivuFilter,
        'prikazi_na_pocetnoj', 'je_novitet', 'je_hit',
        'prioritet_lagera', 'proizvedeno_u_japanu',
        'kategorija', 'brend', 'tagovi',
        ('kreiran', admin.DateFieldListFilter),
    )
    date_hierarchy = 'kreiran'
    ordering = ('-prioritet_lagera', '-kreiran')
    list_editable = (
        'prikazi_na_pocetnoj', 'je_novitet', 'je_hit', 'prioritet_lagera',
        'proizvedeno_u_japanu', 'aktivan', 'na_stanju',
    )
    search_fields = (
        'naziv', 'sifra', 'barkod', 'tagovi__naziv',
        'kategorija__naziv', 'kategorija__roditelj__naziv',
        'odoo_template_id', 'meta_title', 'meta_description',
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(varijacije_broj=Count('varijacije', distinct=True))

    def get_search_results(self, request, queryset, search_term):
        """
        Autocomplete filteri po kontekstu (model_name u /admin/autocomplete/).
        - advisor set / dwell: samo na stanju
        - bundle linije: aktivni artikli (mogu se dodati u set)
        """
        queryset, use_distinct = super().get_search_results(
            request, queryset, search_term,
        )
        model_name = (request.GET.get('model_name') or '').lower()
        if model_name in (
            'advisorbeginnersetitem',
            'productdwellitem',
        ):
            queryset = queryset.filter(aktivan=True, na_stanju=True)
        elif model_name in (
            'akcijbundleline',
            'akcijabundleline',
        ):
            # Bundle set: samo aktivni + na stanju (ne nudi rasprodato)
            queryset = queryset.filter(aktivan=True, na_stanju=True)
        return queryset, use_distinct

    prepopulated_fields = {'slug': ('naziv',)}
    readonly_fields = (
        'kreiran', 'azuriran',
        'pregled_slike_velika', 'odoo_template_id', 'seo_title_preview', 'seo_description_preview',
        'olx_objavi_info', 'olx_listing_id', 'olx_listing_slug', 'olx_listing_url', 'olx_objavljen',
    )
    inlines = [ProductVariationInline, ProductImageInline]

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj:
            # Postavi placeholder-e da korisnik vidi šta će se koristiti ako ostavi prazno
            if 'meta_title' in form.base_fields:
                form.base_fields['meta_title'].widget.attrs.setdefault(
                    'placeholder', obj.seo_title
                )
                form.base_fields['meta_title'].help_text = (
                    'Ostavi prazno za automatski naslov (prikazan gore).'
                )
            if 'meta_description' in form.base_fields:
                form.base_fields['meta_description'].widget.attrs.setdefault(
                    'placeholder', obj.seo_description
                )
                form.base_fields['meta_description'].widget.attrs.setdefault('rows', '4')
                form.base_fields['meta_description'].help_text = (
                    'Ostavi prazno za automatski opis (prikazan gore).'
                )
        return form

    fieldsets = (
        ('Osnovno', {
            'fields': ('naziv', 'slug', 'sifra', 'barkod', 'brend', 'kategorija', 'tagovi'),
        }),
        ('Opis', {
            'fields': ('opis',),
        }),
        ('Slika i cijena', {
            'fields': (
                'slika', 'pregled_slike_velika', 'cijena', 'pakovanje_komada',
                'akcija_postotak', 'akcijska_cijena', 'akcija_do',
                'na_stanju', 'stanje',
            ),
            'description': (
                'Akcija: unesite popust (%) za automatski izračun akcijske cijene, '
                'ili ručno unesite akcijsku cijenu. '
                'Pakovanje: ako je cijena za pakovanje (npr. 9 kom), unesi broj komada — '
                'kupac vidi „Pakovanje 9 kom.” da ne pomisli da je cijena po komadu. '
                'Upload slike: AVIF max 15KB + responsive 120/200/320w.'
            ),
        }),
        ('Prikaz', {
            'fields': (
                'prikazi_na_pocetnoj', 'je_novitet', 'je_hit',
                'prioritet_lagera', 'proizvedeno_u_japanu', 'aktivan',
            ),
            'description': (
                '„Proizvedeno u Japanu” — na sajtu se ispod naziva prikaže badge Made in Japan. '
                '„Redukovanje lagera”: Normalno / Favorizuj / Hit — prioritet samo među '
                'relevantnim rezultatima pretrage, kategorije i preporuka (ne gura nerelevantne).'
            ),
        }),
        ('SEO (Google) — artikal', {
            'fields': (
                'seo_title_preview', 'meta_title',
                'seo_description_preview', 'meta_description',
                'h1_naslov', 'seo_tekst_iznad', 'seo_tekst_ispod',
            ),
            'description': (
                'Sva polja su <strong>opcionalna</strong>. Sistem automatski radi: '
                '<em>Naziv | Brend | opremazaribolov.ba</em> + opis s benefitima.<br>'
                '<strong>Ručno popuni samo</strong> za bestsellere / skupe / prioritetne artikle.<br>'
                '• Title 50–60 znakova · Description 140–160 · H1 = kupcu jasno ime proizvoda<br>'
                '• SEO tekst: rijetko potreban na artiklu (bolje dobar <em>Opis proizvoda</em>)<br>'
                '• Rich results: cijena, stock, brand, SKU idu automatski u Product schema'
            ),
        }),
        ('OLX / Pik', {
            'fields': (
                'olx_objavi_info', 'olx_listing_id', 'olx_listing_slug',
                'olx_listing_url', 'olx_objavljen',
            ),
            'description': (
                'Dugme <strong>Objavi na OLX / Pik</strong> je pored Save (dolje na stranici). '
                'OLX Shop oglasi se ne vide na javnom profilu — provjeri u Pik/OLX aplikaciji: '
                '<strong>Moj OLX → Aktivni oglasi</strong>, ili pretraga na olx.ba.'
            ),
        }),
        ('Odoo', {
            'fields': ('odoo_template_id',),
            'classes': ('collapse',),
        }),
        ('Datumi', {
            'fields': ('kreiran', 'azuriran'),
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/olx-objavi/',
                self.admin_site.admin_view(self.olx_publish_view),
                name='EcommerceApp_product_olx_publish',
            ),
            path(
                'import-odoo/',
                self.admin_site.admin_view(self.odoo_import_view),
                name='EcommerceApp_product_odoo_import',
            ),
            path(
                'brzi-unos/',
                self.admin_site.admin_view(self.brzi_unos_view),
                name='EcommerceApp_product_brzi_unos',
            ),
            path(
                'brzi-unos/<int:product_id>/',
                self.admin_site.admin_view(self.brzi_unos_aktivacija_view),
                name='EcommerceApp_product_brzi_unos_aktivacija',
            ),
        ]
        return custom_urls + urls

    def change_view(self, request, object_id, form_url='', extra_context=None):
        from django.conf import settings

        extra_context = extra_context or {}
        extra_context['olx_api_configured'] = bool(settings.OLX_API_TOKEN)
        if object_id:
            extra_context['olx_publish_url'] = reverse(
                'admin:EcommerceApp_product_olx_publish',
                args=[object_id],
            )
            obj = self.get_object(request, object_id)
            if obj and obj.olx_listing_id:
                extra_context['olx_publish_label'] = 'Ažuriraj na OLX / Pik'
            else:
                extra_context['olx_publish_label'] = 'Objavi na OLX / Pik'
        return super().change_view(request, object_id, form_url, extra_context)

    def olx_publish_view(self, request, object_id):
        from django.conf import settings
        from django.utils import timezone

        from .olx_api import OlxApiError, publish_product_to_olx

        if request.method != 'POST':
            return redirect('admin:EcommerceApp_product_change', object_id)

        if not self.has_change_permission(request):
            messages.error(request, 'Nemate dozvolu za izmjenu artikla.')
            return redirect('admin:EcommerceApp_product_changelist')

        if not settings.OLX_API_TOKEN:
            messages.error(request, 'OLX_API_TOKEN nije postavljen u okruženju.')
            return redirect('admin:EcommerceApp_product_change', object_id)

        product = self.get_object(request, object_id)
        if product is None:
            messages.error(request, 'Artikal nije pronađen.')
            return redirect('admin:EcommerceApp_product_changelist')

        try:
            result = publish_product_to_olx(product)
            product.olx_listing_id = result['id']
            product.olx_listing_slug = result.get('slug', '') or ''
            product.olx_listing_url = result.get('url', '') or ''
            product.olx_objavljen = timezone.now()
            product.save(update_fields=[
                'olx_listing_id', 'olx_listing_slug', 'olx_listing_url', 'olx_objavljen',
            ])
            if result.get('status') == 'active':
                messages.success(
                    request,
                    'Artikal je aktivan na OLX/Pik. Provjeri u aplikaciji: Moj OLX → Aktivni oglasi. '
                    f'Link: {result.get("url", "")}',
                )
            else:
                messages.warning(
                    request,
                    'Oglas je poslan na OLX/Pik, ali nije postao aktivan. '
                    'Provjeri Neaktivne oglase u Pik/OLX aplikaciji. '
                    f'Link: {result.get("url", "")}',
                )
        except OlxApiError as exc:
            messages.error(request, f'OLX/Pik objava nije uspjela: {exc}')
            logger.warning('OLX admin objava %s nije uspjela: %s', product.slug, exc)
        except Exception as exc:
            logger.exception('OLX admin objava artikla %s', product.slug)
            messages.error(request, f'Neočekivana greška pri objavi: {exc}')

        return redirect('admin:EcommerceApp_product_change', object_id)

    def _build_import_job_from_form(self, cleaned, client):
        template_ids = fetch_template_ids_from_odoo(
            cleaned['odoo_category_id'],
            include_children=cleaned['ukljuci_podkategorije'],
            client=client,
        )
        return {
            'template_ids': template_ids,
            'position': 0,
            'stats': _empty_import_stats(total=len(template_ids)),
            'options': {
                'odoo_category_id': cleaned['odoo_category_id'],
                'django_category_id': cleaned['kategorija'].pk if cleaned['kategorija'] else None,
                'include_children': cleaned['ukljuci_podkategorije'],
                'update_existing': cleaned['azuriraj_postojece'],
                'load_images': cleaned['ucitaj_slike'],
                'stock_only': cleaned['samo_stanje'],
                'images_only': cleaned['samo_slike'],
                'names_only': cleaned.get('samo_naziv', False),
                'excluded_brand_ids': [
                    brand.pk for brand in cleaned['preskoci_brendovi']
                ],
            },
        }

    def _run_import_job_chunk(self, request, job, *, django_category=None):
        client = OdooClient.from_settings()
        template_ids = job['template_ids']
        stats = job.get('stats') or _empty_import_stats(total=len(template_ids))
        start = job.get('position', 0)
        options = job['options']

        django_category_id = job.get('django_category_id') or options.get('django_category_id')
        if django_category is None and django_category_id:
            django_category = Category.objects.filter(pk=django_category_id).first()

        chunk_stats = import_products_from_odoo(
            options['odoo_category_id'],
            django_category=django_category,
            include_children=options['include_children'],
            update_existing=options['update_existing'],
            load_images=options['load_images'],
            stock_only=options['stock_only'],
            images_only=options.get('images_only', False),
            names_only=options.get('names_only', False),
            excluded_brand_ids=options['excluded_brand_ids'],
            client=client,
            template_ids=template_ids,
            start=start,
            limit=import_chunk_size(
                load_images=options['load_images'],
                stock_only=options['stock_only'],
                images_only=options.get('images_only', False),
                names_only=options.get('names_only', False),
            ),
        )
        stats = merge_import_stats(stats, chunk_stats)
        job['position'] = stats['position']
        job['stats'] = stats
        return job, stats

    def _finish_import_success(self, request, stats, *, names_only=False):
        request.session.pop(ODOO_IMPORT_SESSION_KEY, None)
        if names_only:
            messages.success(
                request,
                (
                    f'Odoo sync naziva završen: {stats["azurirano"]} artikala usklađeno, '
                    f'{stats["preskoceno"]} preskočenih (nisu pronađeni na sajtu ili zaštićen brend). '
                    f'Varijacije ažurirane: {stats["varijacija_azurirano"]}.'
                ),
            )
        else:
            messages.success(
                request,
                (
                    f'Odoo import završen: {stats["kreirano"]} novih, '
                    f'{stats["azurirano"]} ažuriranih, {stats["preskoceno"]} preskočenih. '
                    f'Varijacije: {stats["varijacija_kreirano"]} novih, '
                    f'{stats["varijacija_azurirano"]} ažuriranih.'
                ),
            )
        if stats['greske']:
            messages.warning(
                request,
                f'Greške ({len(stats["greske"])}): ' + '; '.join(stats['greske'][:5]),
            )
        return redirect('admin:EcommerceApp_product_changelist')

    def brzi_unos_view(self, request):
        """Korak 1: sken / šifra / barkod / naziv → pronađi postojeći artikal."""
        from .quick_activation import find_products, find_single_product, normalize_scan_code

        if not self.has_change_permission(request):
            messages.error(request, 'Nemate dozvolu za izmjenu artikala.')
            return redirect('admin:EcommerceApp_product_changelist')

        query = normalize_scan_code(request.GET.get('q') or request.POST.get('q') or '')
        matches = []
        not_found = False

        if request.method == 'POST' or query:
            if not query:
                messages.warning(request, 'Unesi šifru, barkod ili naziv — ili skeniraj barkod.')
            else:
                product, multi = find_single_product(query)
                if product is not None:
                    return redirect(
                        'admin:EcommerceApp_product_brzi_unos_aktivacija',
                        product_id=product.pk,
                    )
                matches = multi if multi is not None else find_products(query)
                if not matches:
                    not_found = True
                    messages.error(
                        request,
                        f'Nijedan artikal nije pronađen za „{query}”. '
                        'Traži po šifri, barkodu ili nazivu (artikal mora već postojati).',
                    )
                elif len(matches) == 1:
                    return redirect(
                        'admin:EcommerceApp_product_brzi_unos_aktivacija',
                        product_id=matches[0].pk,
                    )

        context = {
            **self.admin_site.each_context(request),
            'title': 'Brzi unos / Aktivacija artikala',
            'opts': self.model._meta,
            'query': query,
            'matches': matches,
            'not_found': not_found,
            'has_view_permission': self.has_view_permission(request),
            'has_change_permission': self.has_change_permission(request),
        }
        return render(
            request,
            'admin/EcommerceApp/product/brzi_unos.html',
            context,
        )

    def brzi_unos_aktivacija_view(self, request, product_id):
        """Korak 2: cijena, brend, slika, AI opis → Aktiviraj artikal."""
        from decimal import InvalidOperation
        from urllib.parse import quote_plus

        from .quick_activation import (
            activate_product,
            category_choices,
            parse_price,
            resolve_tags,
            take_off_stock,
        )

        if not self.has_change_permission(request):
            messages.error(request, 'Nemate dozvolu za izmjenu artikala.')
            return redirect('admin:EcommerceApp_product_changelist')

        product = (
            Product.objects.select_related('brend', 'kategorija')
            .prefetch_related('tagovi')
            .filter(pk=product_id)
            .first()
        )
        if product is None:
            messages.error(request, 'Artikal nije pronađen.')
            return redirect('admin:EcommerceApp_product_brzi_unos')

        # Jedan klik: skini sa stanja — samo na_stanju=False, bez forme / validacije
        post_action = (request.POST.get('action') or '').strip() if request.method == 'POST' else ''
        if request.method == 'POST' and post_action == 'off_stock':
            try:
                take_off_stock(product)
                messages.success(
                    request,
                    f'✓ „{product.naziv}” — nije na stanju (sakriven od kupaca).',
                )
            except Exception as exc:
                logger.exception(
                    'Brzi unos: skidanje sa stanja nije uspjelo product_id=%s',
                    product_id,
                )
                messages.error(request, f'Skidanje sa stanja nije uspjelo: {exc}')
            return redirect('admin:EcommerceApp_product_brzi_unos')

        brands = Brand.objects.order_by('naziv')
        categories = category_choices()
        from django.conf import settings as django_settings

        olx_configured = bool(getattr(django_settings, 'OLX_API_TOKEN', None))
        form_errors = []
        pack_n = product.pakovanje_komada or 0
        form_data = {
            'cijena': str(product.cijena) if product.cijena is not None else '',
            'brend_id': str(product.brend_id or ''),
            'kategorija_id': str(product.kategorija_id or ''),
            'opis': (product.opis or '').strip(),
            # Polje prazno — samo ručni unos novih tagova zarezom
            'tagovi': '',
            'barkod': (product.barkod or '').strip(),
            'je_pakovanje': '1' if pack_n and pack_n > 1 else '',
            'pakovanje_komada': str(pack_n) if pack_n and pack_n > 1 else '',
            'proizvedeno_u_japanu': '1' if product.proizvedeno_u_japanu else '',
            'objavi_olx': '',
        }

        # Aktivacija: samo eksplicitni action=activate (ne diraj off_stock ni prazan POST)
        if request.method == 'POST' and post_action == 'activate':
            form_data['cijena'] = (request.POST.get('cijena') or '').strip()
            form_data['brend_id'] = (request.POST.get('brend_id') or '').strip()
            form_data['kategorija_id'] = (request.POST.get('kategorija_id') or '').strip()
            form_data['opis'] = (request.POST.get('opis') or '').strip()
            form_data['tagovi'] = (request.POST.get('tagovi') or '').strip()
            form_data['barkod'] = (request.POST.get('barkod') or '').strip()
            form_data['je_pakovanje'] = (
                '1'
                if (request.POST.get('je_pakovanje') or '').strip()
                in ('1', 'true', 'on', 'yes')
                else ''
            )
            form_data['pakovanje_komada'] = (
                request.POST.get('pakovanje_komada') or ''
            ).strip()
            form_data['proizvedeno_u_japanu'] = (
                '1'
                if (request.POST.get('proizvedeno_u_japanu') or '').strip()
                in ('1', 'true', 'on', 'yes')
                else ''
            )
            form_data['objavi_olx'] = (
                '1'
                if (request.POST.get('objavi_olx') or '').strip()
                in ('1', 'true', 'on', 'yes')
                else ''
            )
            # Galerija ili kamera — oba file inputa dijele name="slika"
            image_upload = request.FILES.get('slika') or request.FILES.get('slika_kamera')
            keep_image = request.POST.get('keep_existing_image') == '1'
            extra_images = request.FILES.getlist('dodatne_slike')

            brend = None
            if form_data['brend_id']:
                brend = brands.filter(pk=form_data['brend_id']).first()

            kategorija = None
            if form_data['kategorija_id']:
                try:
                    kategorija = Category.objects.filter(
                        pk=int(form_data['kategorija_id']),
                        aktivan=True,
                    ).first()
                except (TypeError, ValueError):
                    kategorija = None

            try:
                cijena = parse_price(form_data['cijena'])
            except (InvalidOperation, ValueError):
                form_errors.append('Unesi ispravnu cijenu (npr. 12.90).')
                cijena = None

            if form_data['brend_id']:
                if brend is None:
                    form_errors.append('Odabrani brend ne postoji.')
            else:
                form_errors.append('Izaberi brend.')

            if form_data['kategorija_id']:
                if kategorija is None:
                    form_errors.append('Odabrana kategorija ne postoji.')
            else:
                form_errors.append('Izaberi kategoriju.')

            if not image_upload and not (product.slika and product.slika.name):
                form_errors.append('Dodaj sliku artikla (galerija ili kamera).')
            elif not image_upload and product.slika and product.slika.name:
                keep_image = True

            pack_value = None
            if form_data['je_pakovanje']:
                raw_pack = form_data['pakovanje_komada']
                try:
                    pack_value = int(raw_pack) if raw_pack else 0
                except (TypeError, ValueError):
                    pack_value = 0
                    form_errors.append('Pakovanje: unesi cijeli broj komada (npr. 9).')
                if pack_value and pack_value <= 1:
                    form_errors.append('Pakovanje: količina mora biti najmanje 2 komada.')
                    pack_value = None
            # bez checkboxa = isključi pakovanje (po komadu)

            if not form_errors and cijena is not None:
                try:
                    tagovi = resolve_tags(form_data['tagovi'])
                    activate_product(
                        product,
                        cijena=cijena,
                        brend=brend,
                        kategorija=kategorija,
                        image_upload=image_upload,
                        keep_existing_image=keep_image and not image_upload,
                        opis=form_data['opis'],
                        tagovi=tagovi,
                        barkod=form_data['barkod'],
                        extra_images=extra_images,
                        set_pakovanje=True,
                        pakovanje_komada=pack_value if form_data['je_pakovanje'] else None,
                        proizvedeno_u_japanu=bool(form_data['proizvedeno_u_japanu']),
                    )
                    tag_note = f', {len(tagovi)} tag(ova)' if tagovi else ''
                    cat_note = f', {kategorija.naziv}' if kategorija else ''
                    extra_note = f', +{len(extra_images)} slika' if extra_images else ''
                    pack_note = ''
                    if form_data['je_pakovanje'] and pack_value and pack_value > 1:
                        pack_note = f', pakovanje {pack_value} kom.'
                    japan_note = ', Made in Japan' if form_data['proizvedeno_u_japanu'] else ''
                    messages.success(
                        request,
                        f'✓ „{product.naziv}” je aktivan na webshopu '
                        f'({cijena} KM'
                        f'{f", {brend.naziv}" if brend else ""}'
                        f'{cat_note}'
                        f'{tag_note}'
                        f'{extra_note}'
                        f'{pack_note}'
                        f'{japan_note}'
                        f', na stanju).',
                    )

                    # Opcionalno: odmah objavi na OLX/Pik
                    if form_data['objavi_olx']:
                        if not olx_configured:
                            messages.warning(
                                request,
                                'OLX nije konfigurisan (OLX_API_TOKEN) — artikal je aktivan, ali nije objavljen.',
                            )
                        else:
                            try:
                                from django.utils import timezone as dj_tz

                                from .olx_api import OlxApiError, publish_product_to_olx

                                olx_result = publish_product_to_olx(product)
                                product.olx_listing_id = olx_result['id']
                                product.olx_listing_slug = olx_result.get('slug', '') or ''
                                product.olx_listing_url = olx_result.get('url', '') or ''
                                product.olx_objavljen = dj_tz.now()
                                product.save(
                                    update_fields=[
                                        'olx_listing_id',
                                        'olx_listing_slug',
                                        'olx_listing_url',
                                        'olx_objavljen',
                                    ]
                                )
                                olx_url = olx_result.get('url') or ''
                                if olx_result.get('status') == 'active':
                                    messages.success(
                                        request,
                                        f'Objavljeno na OLX/Pik. {olx_url}'.strip(),
                                    )
                                else:
                                    messages.warning(
                                        request,
                                        'OLX oglas poslan, ali nije aktivan — provjeri Neaktivne u Pik/OLX. '
                                        f'{olx_url}'.strip(),
                                    )
                            except OlxApiError as olx_exc:
                                messages.error(
                                    request,
                                    f'Artikal je aktivan, ali OLX objava nije uspjela: {olx_exc}',
                                )
                                logger.warning(
                                    'Brzi unos OLX %s: %s', product.slug, olx_exc
                                )
                            except Exception as olx_exc:
                                logger.exception(
                                    'Brzi unos OLX neočekivano product_id=%s', product_id
                                )
                                messages.error(
                                    request,
                                    f'Artikal je aktivan, ali OLX objava nije uspjela: {olx_exc}',
                                )

                    return redirect('admin:EcommerceApp_product_brzi_unos')
                except Exception as exc:
                    logger.exception(
                        'Brzi unos: aktivacija nije uspjela za product_id=%s',
                        product_id,
                    )
                    form_errors.append(f'Aktivacija nije uspjela: {exc}')

        # Osvježi product (npr. nakon greške)
        product.refresh_from_db()
        current_image_url = ''
        if product.slika and product.slika.name:
            try:
                current_image_url = product.slika.url
            except Exception:
                current_image_url = ''

        existing_extra = []
        for img in product.dodatne_slike.all().order_by('redoslijed', 'id')[:12]:
            try:
                url = img.slika.url if img.slika else ''
            except Exception:
                url = ''
            if url:
                existing_extra.append({'id': img.pk, 'url': url})

        # Google Images — samo naziv artikla
        google_query = (product.naziv or '').strip()
        google_images_url = (
            'https://www.google.com/search?tbm=isch&q=' + quote_plus(google_query)
            if google_query else ''
        )

        # ChatGPT — naziv + veći opis + tagovi
        chatgpt_url = ''
        if google_query:
            chatgpt_prompt = f'{google_query} veci opis za ovaj artikal i tagove'
            chatgpt_url = 'https://chatgpt.com/?q=' + quote_plus(chatgpt_prompt)

        context = {
            **self.admin_site.each_context(request),
            'title': f'Aktivacija: {product.naziv}',
            'opts': self.model._meta,
            'product': product,
            'brands': brands,
            'categories': categories,
            'categories_json': categories,
            'form_data': form_data,
            'form_errors': form_errors,
            'current_image_url': current_image_url,
            'existing_extra_images': existing_extra,
            'google_images_url': google_images_url,
            'google_query': google_query,
            'chatgpt_url': chatgpt_url,
            'olx_configured': olx_configured,
            'has_view_permission': self.has_view_permission(request),
            'has_change_permission': self.has_change_permission(request),
            'scan_url': reverse('admin:EcommerceApp_product_brzi_unos'),
        }
        return render(
            request,
            'admin/EcommerceApp/product/brzi_unos_aktivacija.html',
            context,
        )

    def odoo_import_view(self, request):
        get_token(request)

        if not odoo_je_konfigurisan():
            messages.error(
                request,
                'Odoo nije konfigurisan. U .env postavite ODOO_URL, ODOO_DB, ODOO_USERNAME i ODOO_API_KEY.',
            )
            return redirect('admin:EcommerceApp_product_changelist')

        odoo_choices = []
        odoo_error = None
        try:
            client = OdooClient.from_settings()
            odoo_choices = client.list_product_categories()
        except OdooError as exc:
            odoo_error = str(exc)
        except Exception as exc:
            logger.exception('Neočekivana greška pri učitavanju Odoo kategorija')
            odoo_error = f'Neočekivana greška: {exc}'

        import_progress = None
        continue_url = reverse('admin:EcommerceApp_product_odoo_import') + '?continue=1'
        form = OdooImportForm(odoo_category_choices=odoo_choices)

        if request.GET.get('continue') == '1':
            job = request.session.get(ODOO_IMPORT_SESSION_KEY)
            if not job:
                messages.error(request, 'Import sesija je istekla. Pokrenite import ponovo.')
                return redirect('admin:EcommerceApp_product_odoo_import')
            try:
                job, stats = self._run_import_job_chunk(request, job)
                if stats['done']:
                    return self._finish_import_success(
                        request,
                        stats,
                        names_only=bool((job.get('options') or {}).get('names_only')),
                    )

                request.session[ODOO_IMPORT_SESSION_KEY] = job
                request.session.modified = True
                import_progress = {
                    'processed': stats['position'],
                    'total': stats['total'],
                    'percent': int((stats['position'] / stats['total']) * 100) if stats['total'] else 100,
                }
            except OdooError as exc:
                request.session.pop(ODOO_IMPORT_SESSION_KEY, None)
                messages.error(request, str(exc))
            except Exception as exc:
                request.session.pop(ODOO_IMPORT_SESSION_KEY, None)
                logger.exception('Neočekivana greška pri Odoo importu')
                messages.error(
                    request,
                    f'Import nije uspio: {exc}. Pokušajte ponovo ili koristite opciju „Samo ažuriraj stanje”.',
                )

        elif request.method == 'POST':
            form = OdooImportForm(request.POST, odoo_category_choices=odoo_choices)
            if form.is_valid():
                try:
                    client = OdooClient.from_settings()
                    job = self._build_import_job_from_form(form.cleaned_data, client)
                    job, stats = self._run_import_job_chunk(
                        request,
                        job,
                        django_category=form.cleaned_data['kategorija'],
                    )
                    if stats['done']:
                        return self._finish_import_success(
                            request,
                            stats,
                            names_only=bool((job.get('options') or {}).get('names_only')),
                        )

                    request.session[ODOO_IMPORT_SESSION_KEY] = job
                    request.session.modified = True
                    import_progress = {
                        'processed': stats['position'],
                        'total': stats['total'],
                        'percent': int((stats['position'] / stats['total']) * 100) if stats['total'] else 100,
                    }
                except OdooError as exc:
                    request.session.pop(ODOO_IMPORT_SESSION_KEY, None)
                    messages.error(request, str(exc))
                except Exception as exc:
                    request.session.pop(ODOO_IMPORT_SESSION_KEY, None)
                    logger.exception('Neočekivana greška pri Odoo importu')
                    messages.error(
                        request,
                        f'Import nije uspio: {exc}. Pokušajte ponovo ili koristite opciju „Samo ažuriraj stanje”.',
                    )

        context = {
            **self.admin_site.each_context(request),
            'title': 'Import artikala iz Odoo',
            'form': form,
            'odoo_error': odoo_error,
            'import_progress': import_progress,
            'continue_url': continue_url,
            'opts': self.model._meta,
            'has_view_permission': self.has_view_permission(request),
        }
        return render(request, 'admin/EcommerceApp/product/odoo_import.html', context)

    def _bulk_tag_groups(self):
        root_tags = Tag.objects.filter(roditelj__isnull=True).order_by('naziv')
        grouped_tags = []
        all_covered_pks = set()
        for parent in root_tags:
            descendants = list(parent.get_all_descendants(include_self=False))
            grouped_tags.append({
                'parent': parent,
                'children': descendants,
            })
            all_covered_pks.add(parent.pk)
            for descendant in descendants:
                all_covered_pks.add(descendant.pk)
        flat_tags = list(Tag.objects.exclude(pk__in=all_covered_pks).order_by('naziv'))
        return grouped_tags, flat_tags

    def bulk_assign_category(self, request, queryset):
        """
        Lista svih označenih artikala → po artiklu biraš kategoriju
        (ili isključi artikal) → jedan Save na kraju.
        """
        # Prvo artikli bez kategorije (treba dodjela), pa oni koji već imaju.
        from django.db.models import Case, IntegerField, Value, When

        queryset = queryset.select_related(
            'kategorija', 'kategorija__roditelj', 'brend',
        ).annotate(
            _needs_category=Case(
                When(kategorija__isnull=True, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        ).order_by('_needs_category', 'naziv')

        categories = []
        for category in Category.objects.filter(aktivan=True).select_related(
            'roditelj', 'roditelj__roditelj',
        ).order_by('redoslijed', 'naziv'):
            parts = []
            cur = category
            seen = set()
            while cur is not None and cur.pk not in seen:
                seen.add(cur.pk)
                parts.append(cur.naziv or f'#{cur.pk}')
                cur = getattr(cur, 'roditelj', None)
            parts.reverse()
            categories.append({
                'id': category.pk,
                'label': ' → '.join(parts),
            })

        if request.method == 'POST' and 'apply' in request.POST:
            # Sačuvaj SVE artikle koji imaju odabranu kategoriju u formi
            # (checkbox „U grupi” služi samo za batch dodjelu, ne za Save).
            original_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            cat_count = 0
            cleared = 0
            unchanged = 0

            for pk_str in original_ids:
                try:
                    pk = int(pk_str)
                except (TypeError, ValueError):
                    continue

                raw_cat = (request.POST.get(f'kategorija_{pk}') or '').strip()
                if not raw_cat:
                    unchanged += 1
                    continue
                if raw_cat in ('0', '__clear__'):
                    Product.objects.filter(pk=pk).update(kategorija=None)
                    cleared += 1
                    continue
                try:
                    category = Category.objects.get(pk=int(raw_cat), aktivan=True)
                except (Category.DoesNotExist, TypeError, ValueError):
                    unchanged += 1
                    continue
                Product.objects.filter(pk=pk).update(kategorija=category)
                cat_count += 1

            if cat_count:
                self.message_user(
                    request,
                    f'Kategorija postavljena za {cat_count} artikal/a.',
                    messages.SUCCESS,
                )
            if cleared:
                self.message_user(
                    request,
                    f'Kategorija uklonjena sa {cleared} artikal/a.',
                    messages.INFO,
                )
            if unchanged and not cat_count and not cleared:
                self.message_user(
                    request,
                    'Nijedan artikal nije ažuriran — nijedan nema odabranu novu kategoriju. '
                    'Označi grupu → Dodijeli označenima (ili izaberi kategoriju po artiklu) → Save.',
                    messages.WARNING,
                )
            elif unchanged and (cat_count or cleared):
                self.message_user(
                    request,
                    f'{unchanged} artikal/a ostavljen/o bez promjene (nema nove kategorije).',
                    messages.INFO,
                )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Dodjela kategorije po artiklu',
            'queryset': queryset,
            'categories': categories,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_category',
        }
        return render(request, 'admin/EcommerceApp/product/bulk_assign_category.html', context)

    bulk_assign_category.short_description = 'Dodijeli kategoriju (grupe / po artiklu)'

    def bulk_assign_brand(self, request, queryset):
        form = BulkAssignBrandForm(request.POST or None)
        if request.method == 'POST' and 'apply' in request.POST and form.is_valid():
            selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            products = Product.objects.filter(pk__in=selected_ids)
            brand = form.cleaned_data['brend']
            count = products.update(brend=brand)
            self.message_user(
                request,
                f'{count} artikal/a dodijeljeno brendu „{brand}”.',
                messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Dodjela brenda',
            'form': form,
            'form_field': form['brend'],
            'queryset': queryset,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_brand',
            'submit_label': 'Dodijeli brend',
        }
        return render(request, 'admin/EcommerceApp/product/bulk_assign_field.html', context)

    bulk_assign_brand.short_description = 'Dodijeli brend'

    def bulk_assign_pakovanje(self, request, queryset):
        """
        Bulk unos pakovanja: za svaki odabrani artikal unesi svoj broj komada,
        jedan Save sačuva sve.
        """
        queryset = queryset.order_by('naziv')

        if request.method == 'POST' and 'apply' in request.POST:
            selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            updated = 0
            cleared = 0
            skipped = 0
            for pk_str in selected_ids:
                try:
                    pk = int(pk_str)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                raw = (request.POST.get(f'pakovanje_{pk}') or '').strip()
                try:
                    product = Product.objects.get(pk=pk)
                except Product.DoesNotExist:
                    skipped += 1
                    continue
                if raw == '':
                    if product.pakovanje_komada is not None:
                        product.pakovanje_komada = None
                        product.save(update_fields=['pakovanje_komada'])
                        cleared += 1
                    else:
                        skipped += 1
                    continue
                try:
                    n = int(raw)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                if n <= 0:
                    if product.pakovanje_komada is not None:
                        product.pakovanje_komada = None
                        product.save(update_fields=['pakovanje_komada'])
                        cleared += 1
                    else:
                        skipped += 1
                    continue
                if n > 9999:
                    n = 9999
                product.pakovanje_komada = n
                product.save(update_fields=['pakovanje_komada'])
                updated += 1

            if updated:
                self.message_user(
                    request,
                    f'Pakovanje sačuvano za {updated} artikal/a.',
                    messages.SUCCESS,
                )
            if cleared:
                self.message_user(
                    request,
                    f'{cleared} artikal/a vraćeno na „po komadu” (prazno pakovanje).',
                    messages.SUCCESS,
                )
            if skipped and not updated and not cleared:
                self.message_user(
                    request,
                    'Nijedan artikal nije ažuriran. Provjeri unose.',
                    messages.WARNING,
                )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Bulk pakovanje',
            'queryset': queryset,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_pakovanje',
        }
        return render(
            request,
            'admin/EcommerceApp/product/bulk_assign_pakovanje.html',
            context,
        )

    bulk_assign_pakovanje.short_description = 'Bulk pakovanje (po artiklu + jedan Save)'

    def bulk_assign_tags(self, request, queryset):
        """
        Jedno polje za tagove → Primjeni na označene artikle.
        Ako neki označeni već imaju tagove, automatski se predlože.
        Dodaje se samo artiklima koji taj tag još nemaju (bez duplikata).
        """
        queryset = queryset.prefetch_related('tagovi').order_by('naziv')

        if request.method == 'POST' and 'apply' in request.POST:
            selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            # 1) Predloženi tagovi s označenih (checkbox) — dovoljno Save bez unosa u polje
            # 2) Novo polje bulk_tags — opcionalni novi tagovi
            tag_names = []
            seen = set()

            def _add_name(name):
                name = (name or '').strip()
                if not name:
                    return
                key = name.casefold()
                if key in seen:
                    return
                seen.add(key)
                tag_names.append(name)

            for name in request.POST.getlist('suggested_tag'):
                _add_name(name)

            raw_tags = (request.POST.get('bulk_tags') or '').strip()
            for part in raw_tags.replace(';', ',').replace('\n', ',').split(','):
                _add_name(part)

            if not tag_names:
                self.message_user(
                    request,
                    'Nema tagova za primjenu. Ostavi predložene označene ili unesi nove u polje.',
                    messages.ERROR,
                )
                return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

            if not selected_ids:
                self.message_user(
                    request,
                    'Nijedan artikal nije označen.',
                    messages.ERROR,
                )
                return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

            tags = []
            for name in tag_names:
                tag, _created = Tag.get_or_create_by_name(name)
                tags.append(tag)

            products_updated = 0
            tags_added_total = 0
            already_ok = 0
            skipped = 0
            for pk_str in selected_ids:
                try:
                    pk = int(pk_str)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                try:
                    product = Product.objects.prefetch_related('tagovi').get(pk=pk)
                except Product.DoesNotExist:
                    skipped += 1
                    continue
                existing_ids = set(product.tagovi.values_list('pk', flat=True))
                to_add = [t for t in tags if t.pk not in existing_ids]
                if not to_add:
                    already_ok += 1
                    continue
                product.tagovi.add(*to_add)
                products_updated += 1
                tags_added_total += len(to_add)

            tag_label = ', '.join(t.naziv for t in tags)
            if products_updated:
                self.message_user(
                    request,
                    (
                        f'Tagovi „{tag_label}”: dodano na {products_updated} artikal/a '
                        f'({tags_added_total} dodjela bez duplikata).'
                    ),
                    messages.SUCCESS,
                )
            if already_ok:
                self.message_user(
                    request,
                    f'{already_ok} artikal/a već ima sve te tagove — preskočeno (bez duplikata).',
                    messages.INFO,
                )
            if skipped:
                self.message_user(
                    request,
                    f'{skipped} artikal/a preskočeno.',
                    messages.WARNING,
                )
            if not products_updated and not already_ok:
                self.message_user(
                    request,
                    'Nijedan artikal nije ažuriran.',
                    messages.WARNING,
                )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        # Predloži tagove koji već postoje na nekom od označenih → Save ih da svima
        suggested_tags = list(
            Tag.objects.filter(artikli__in=queryset)
            .distinct()
            .order_by('naziv')
        )

        context = {
            **self.admin_site.each_context(request),
            'title': 'Bulk tag — primijeni na označene artikle',
            'queryset': queryset,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_tags',
            'product_count': queryset.count(),
            'suggested_tags': suggested_tags,
        }
        return render(request, 'admin/EcommerceApp/product/bulk_assign_tags.html', context)

    bulk_assign_tags.short_description = 'Bulk tag (jedno polje → svi označeni)'

    def bulk_rename_product_images(self, request, queryset):
        """Preimenuj fajlove slika prema nazivu/slug-u artikla (bez novog uploada)."""
        from .utils.images import rename_product_images_to_title

        renamed = 0
        skipped = 0
        errors = 0
        samples = []

        qs = queryset.prefetch_related('dodatne_slike', 'varijacije')
        for product in qs:
            try:
                results = rename_product_images_to_title(product)
                for kind, label, result in results:
                    if result.get('changed'):
                        renamed += 1
                        if len(samples) < 5:
                            samples.append(
                                f'{product.naziv}: {result["old_name"]} → {result["new_name"]}',
                            )
                    else:
                        skipped += 1
                if not results:
                    skipped += 1
            except Exception as exc:
                errors += 1
                logger.exception('Preimenovanje slika za product pk=%s', product.pk)
                if len(samples) < 8:
                    samples.append(f'{product.naziv}: greška — {exc}')

        level = messages.SUCCESS if renamed and not errors else (
            messages.WARNING if renamed or skipped else messages.ERROR
        )
        msg = (
            f'Preimenovanje slika: {renamed} fajl(ova) preimenovano, '
            f'{skipped} već u redu / bez slike, {errors} greška(ka).'
        )
        if samples:
            msg += ' Primjeri: ' + ' · '.join(samples)
        self.message_user(request, msg, level)

    bulk_rename_product_images.short_description = (
        'Preimenuj slike prema nazivu artikla (bez re-uploada)'
    )

    def bulk_proizvedeno_u_japanu(self, request, queryset):
        count = queryset.update(proizvedeno_u_japanu=True)
        self.message_user(
            request,
            f'{count} artikal/a označeno kao proizvedeno u Japanu.',
            messages.SUCCESS,
        )

    bulk_proizvedeno_u_japanu.short_description = 'Proizvedeno u Japanu'

    def bulk_ukloni_japan(self, request, queryset):
        count = queryset.update(proizvedeno_u_japanu=False)
        self.message_user(
            request,
            f'Uklonjena oznaka „Proizvedeno u Japanu” sa {count} artikal/a.',
            messages.SUCCESS,
        )

    bulk_ukloni_japan.short_description = 'Ukloni oznaku Japan'

    def bulk_objavi_na_olx_pik(self, request, queryset):
        from django.conf import settings
        from django.utils import timezone

        from .olx_api import OlxApiError, publish_product_to_olx

        if not settings.OLX_API_TOKEN:
            self.message_user(
                request,
                'OLX_API_TOKEN nije postavljen u okruženju.',
                messages.ERROR,
            )
            return

        success = 0
        inactive = 0
        errors = 0
        error_details = []
        for product in queryset.select_related('brend', 'kategorija').prefetch_related('dodatne_slike'):
            try:
                result = publish_product_to_olx(product)
                product.olx_listing_id = result['id']
                product.olx_listing_slug = result.get('slug', '') or ''
                product.olx_listing_url = result.get('url', '') or ''
                product.olx_objavljen = timezone.now()
                product.save(update_fields=[
                    'olx_listing_id', 'olx_listing_slug', 'olx_listing_url', 'olx_objavljen',
                ])
                if result.get('status') == 'active':
                    success += 1
                else:
                    inactive += 1
            except OlxApiError as exc:
                errors += 1
                detail = f'{product.naziv}: {exc}'
                if exc.details:
                    detail += f' ({exc.details})'
                error_details.append(detail)
                logger.warning('OLX objava %s nije uspjela: %s', product.slug, exc)
            except Exception as exc:
                errors += 1
                error_details.append(f'{product.naziv}: {exc}')
                logger.exception('OLX objava %s nije uspjela', product.slug)

        if success:
            self.message_user(request, f'{success} artikal/a aktivno na OLX/Pik.', messages.SUCCESS)
        if inactive:
            self.message_user(
                request,
                f'{inactive} artikal/a kreirano kao NEAKTIVNO — aktiviraj u OLX/Pik profilu (Neaktivni oglasi).',
                messages.WARNING,
            )
        if errors:
            self.message_user(
                request,
                f'{errors} artikal/a nije objavljeno (greška API-ja).',
                messages.ERROR,
            )
            for detail in error_details[:5]:
                self.message_user(request, detail, messages.ERROR)

    bulk_objavi_na_olx_pik.short_description = 'Objavi na OLX / Pik'

    def bulk_merge_products(self, request, queryset):
        selected = queryset.distinct()
        if selected.count() < 2:
            self.message_user(request, 'Odaberite najmanje 2 artikla za spajanje.', messages.ERROR)
            return

        if 'apply' in request.POST:
            form = MergeProductsForm(request.POST, selected_products=selected)
            if form.is_valid():
                selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
                products = Product.objects.filter(pk__in=selected_ids).distinct()
                try:
                    result = merge_products(
                        products,
                        form.cleaned_data['glavni_artikal'],
                        new_name=form.cleaned_data.get('naziv'),
                    )
                    self.message_user(
                        request,
                        (
                            f'Artikli spojeni u „{result["primary"].naziv}”. '
                            f'Varijacije: {result["created_variations"]} novih, '
                            f'{result["updated_variations"]} ažuriranih. '
                            f'Uklonjeno {result["deleted_products"]} duplih artikala.'
                        ),
                        messages.SUCCESS,
                    )
                    return HttpResponseRedirect(
                        reverse('admin:EcommerceApp_product_change', args=[result['primary'].pk]),
                    )
                except ProductMergeError as exc:
                    self.message_user(request, str(exc), messages.ERROR)
        else:
            form = MergeProductsForm(selected_products=selected)

        context = {
            **self.admin_site.each_context(request),
            'title': 'Spoji artikle u varijante',
            'form': form,
            'queryset': selected,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_merge_products',
        }
        return render(request, 'admin/EcommerceApp/product/bulk_merge_products.html', context)

    bulk_merge_products.short_description = 'Spoji u jedan artikal (varijante)'

    def bulk_split_variations(self, request, queryset):
        selected = queryset.distinct().prefetch_related('varijacije')
        with_vars = [product for product in selected if product.varijacije.exists()]
        if not with_vars:
            self.message_user(
                request,
                'Odaberite artikal koji ima varijacije da ga rastavite na zasebne artikle.',
                messages.ERROR,
            )
            return

        if 'apply' in request.POST:
            created_total = 0
            split_total = 0
            errors = 0
            last_primary = None
            for product in with_vars:
                try:
                    result = split_product_variations(product)
                except ProductMergeError as exc:
                    errors += 1
                    self.message_user(request, str(exc), messages.ERROR)
                    continue
                created_total += len(result['created_products'])
                split_total += result['split_count']
                last_primary = result['primary']
            if split_total:
                self.message_user(
                    request,
                    (
                        f'Rastavljeno {len(with_vars) - errors} artikl(a) u {split_total} zasebnih. '
                        f'Novo kreirano: {created_total}.'
                    ),
                    messages.SUCCESS,
                )
            if last_primary and len(with_vars) == 1 and not errors:
                return HttpResponseRedirect(
                    reverse('admin:EcommerceApp_product_change', args=[last_primary.pk]),
                )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Rastavi varijacije u zasebne artikle',
            'queryset': with_vars,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_split_variations',
        }
        return render(request, 'admin/EcommerceApp/product/bulk_split_variations.html', context)

    bulk_split_variations.short_description = 'Vrati varijacije u zasebne artikle'

    @admin.display(description='Upute')
    def olx_objavi_info(self, obj):
        from django.conf import settings

        if not obj or not obj.pk:
            return 'Sačuvaj artikal, zatim klikni „Objavi na OLX / Pik” pored dugmeta Save (dolje).'
        if not settings.OLX_API_TOKEN:
            return mark_safe(
                '<span style="color:#ba2121;">OLX_API_TOKEN nije postavljen u okruženju.</span>',
            )
        if obj.olx_listing_id:
            return format_html(
                'Objavljen (ID {}). Klikni <strong>Ažuriraj na OLX / Pik</strong> pored Save '
                'za ponovno slanje cijene i slika.',
                obj.olx_listing_id,
            )
        return mark_safe(
            'Nije objavljen. Klikni <strong>Objavi na OLX / Pik</strong> pored Save (dolje na stranici).',
        )

    @admin.display(description='Varijacije', ordering='varijacije_broj')
    def varijacije_broj(self, obj):
        n = getattr(obj, 'varijacije_broj', None)
        if n is None:
            n = obj.varijacije.count()
        return n or '—'

    @admin.display(description='MT u nazivu')
    def mt_sifra_u_nazivu(self, obj):
        from .product_options import extract_mt_codes

        codes = extract_mt_codes(obj.naziv)
        return ', '.join(codes) or '—'

    @admin.display(description='Dodano', ordering='kreiran')
    def datum_dodavanja(self, obj):
        if not obj.kreiran:
            return '—'
        from django.utils import timezone
        local = timezone.localtime(obj.kreiran)
        return local.strftime('%d.%m.%Y. %H:%M')

    @admin.display(description='OLX/Pik')
    def olx_status(self, obj):
        if obj.olx_listing_id:
            if obj.olx_listing_url:
                return format_html(
                    '<a href="{}" target="_blank" rel="noopener">{}</a>',
                    obj.olx_listing_url,
                    obj.olx_listing_id,
                )
            return str(obj.olx_listing_id)
        return '—'

    @admin.display(description='Slika')
    def pregled_slike(self, obj):
        if obj and obj.slika:
            try:
                return format_html(
                    '<img src="{}" style="height:40px;border-radius:4px;" />',
                    obj.slika.url,
                )
            except Exception:
                return '—'
        return '—'

    @admin.display(description='Pregled slike')
    def pregled_slike_velika(self, obj):
        if obj and obj.slika:
            try:
                return format_html(
                    '<img src="{}" style="max-height:200px;border-radius:8px;" />',
                    obj.slika.url,
                )
            except Exception:
                return 'Nema slike'
        return 'Nema slike'

    @admin.display(description='Automatski SEO naslov')
    def seo_title_preview(self, obj):
        if obj:
            try:
                return format_html(
                    '<div style="padding:8px 12px; background:#f8f9fa; border:1px solid #ddd; border-radius:4px; font-size:13px; margin:2px 0;">'
                    '<strong>Koristiće se ako polje ostane prazno:</strong><br>'
                    '<span style="color:#0a66c2; font-weight:500;">{}</span>'
                    '</div>',
                    obj.seo_title
                )
            except Exception:
                return '—'
        return '—'

    @admin.display(description='Automatski meta opis')
    def seo_description_preview(self, obj):
        if obj:
            try:
                return format_html(
                    '<div style="padding:8px 12px; background:#f8f9fa; border:1px solid #ddd; border-radius:4px; font-size:13px; line-height:1.4; margin:2px 0;">'
                    '<strong>Koristiće se ako polje ostane prazno:</strong><br>'
                    '<span style="color:#0a66c2;">{}</span>'
                    '</div>',
                    obj.seo_description
                )
            except Exception:
                return '—'
        return '—'


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ('sender_type', 'staff_user', 'body', 'created_at', 'read_by_staff', 'read_by_customer')
    can_delete = False


@admin.register(ChatConversation)
class ChatConversationAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'display_email', 'is_registered', 'staff_unread_count', 'status', 'last_message_at')
    list_filter = ('status', 'staff_unread_count')
    search_fields = ('guest_name', 'guest_email', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('session_key', 'created_at', 'last_message_at', 'staff_unread_count', 'customer_unread_count')
    inlines = [ChatMessageInline]


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'sender_type', 'body_preview', 'created_at', 'read_by_staff', 'read_by_customer')
    list_filter = ('sender_type', 'read_by_staff', 'read_by_customer')
    search_fields = ('body', 'conversation__guest_email', 'conversation__guest_name')
    readonly_fields = ('conversation', 'sender_type', 'staff_user', 'body', 'created_at', 'read_by_staff', 'read_by_customer')

    @admin.display(description='Poruka')
    def body_preview(self, obj):
        return obj.body[:80]


@admin.register(LiveVisitorOffer)
class LiveVisitorOfferAdmin(admin.ModelAdmin):
    list_display = ('product', 'session_key', 'user', 'discount_percent', 'show_popup', 'added_to_cart', 'poslao', 'azurirano')
    list_filter = ('show_popup', 'added_to_cart', 'azurirano')
    search_fields = ('session_key', 'user__email', 'product__naziv')
    readonly_fields = ('kreirano', 'azurirano')
    ordering = ('-azurirano',)
    autocomplete_fields = ('product', 'user', 'poslao')


@admin.register(OnlineGiftCampaign)
class OnlineGiftCampaignAdmin(admin.ModelAdmin):
    list_display = (
        'naziv', 'aktivan', 'automatic', 'audience', 'prize_type', 'win_chance_percent',
        'only_tracked_online', 'product', 'discount_percent', 'discount_km', 'azurirano',
    )
    list_filter = ('aktivan', 'automatic', 'audience', 'prize_type', 'only_tracked_online')
    search_fields = ('naziv', 'naslov', 'product__naziv')
    autocomplete_fields = ('product',)
    readonly_fields = ('kreirano', 'azurirano')
    fieldsets = (
        ('Osnovno', {
            'fields': (
                'naziv', 'aktivan', 'automatic', 'audience', 'only_tracked_online',
                'naslov', 'poruka', 'popup_delay_seconds', 'once_per_visitor',
            ),
            'description': (
                'Nagrada za kupce ONLINE na sajtu. '
                'Automatski: iskače svima jednom. '
                'Manuelno: isključi „Automatski” i pusti pored kupca u Uživo analitici.'
            ),
        }),
        ('Nagrada', {
            'fields': ('prize_type', 'product', 'discount_percent', 'discount_km', 'win_chance_percent'),
            'description': (
                '① Gratis artikal (dostava se naplaćuje). '
                '② % na narudžbu. ③ KM. '
                '④ Besplatna dostava — jedina nagrada s gratis poštom.'
            ),
        }),
        ('Sistem', {'fields': ('kreirano', 'azurirano')}),
    )


@admin.register(OnlineGiftPush)
class OnlineGiftPushAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'campaign', 'session_key', 'user', 'staff',
        'played', 'dismissed', 'kreirano',
    )
    list_filter = ('played', 'dismissed', 'kreirano')
    search_fields = ('session_key', 'user__email')
    readonly_fields = (
        'campaign', 'session_key', 'user', 'staff',
        'played', 'dismissed', 'kreirano', 'azurirano',
    )
    ordering = ('-kreirano',)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if obj.aktivan:
            OnlineGiftCampaign.objects.filter(aktivan=True).exclude(pk=obj.pk).update(aktivan=False)


@admin.register(OnlineGiftClaim)
class OnlineGiftClaimAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'campaign', 'won', 'prize_type', 'user', 'session_key',
        'reward_claimed', 'reward_consumed', 'order', 'kreirano',
    )
    list_filter = ('won', 'prize_type', 'reward_claimed', 'reward_consumed')
    search_fields = (
        'session_key', 'user__email', 'campaign__naziv', 'order__broj',
    )
    readonly_fields = (
        'campaign', 'session_key', 'user', 'won', 'prize_type', 'product',
        'discount_percent', 'discount_km', 'reward_claimed', 'reward_consumed',
        'order', 'kreirano',
    )
    ordering = ('-kreirano',)


@admin.register(LiveVisitor)
class LiveVisitorAdmin(admin.ModelAdmin):
    list_display = ('ime', 'email', 'grad', 'user', 'last_seen', 'first_seen', 'session_key')
    list_filter = ('last_seen', 'grad')
    search_fields = ('ime', 'email', 'grad', 'session_key', 'user__email', 'ip_adresa')
    readonly_fields = ('first_seen', 'last_seen', 'session_key')
    ordering = ('-last_seen',)


@admin.register(CityVisitTotal)
class CityVisitTotalAdmin(admin.ModelAdmin):
    list_display = ('grad', 'broj_posjeta', 'azurirano')
    search_fields = ('grad',)
    ordering = ('-broj_posjeta', 'grad')
    readonly_fields = ('azurirano',)


@admin.register(StaffSiteEvent)
class StaffSiteEventAdmin(admin.ModelAdmin):
    list_display = ('tip', 'naslov', 'ime', 'email', 'grad', 'kreirano')
    list_filter = ('tip', 'kreirano')
    search_fields = ('naslov', 'poruka', 'ime', 'email', 'grad', 'session_key')
    readonly_fields = ('kreirano',)
    ordering = ('-kreirano',)


@admin.register(ActiveCartItem)
class ActiveCartItemAdmin(admin.ModelAdmin):
    list_display = (
        'naziv', 'varijacija_naziv', 'kolicina', 'cijena', 'ukupno',
        'user', 'session_key', 'dodano', 'azurirano',
    )
    list_filter = ('dodano', 'azurirano')
    search_fields = ('naziv', 'varijacija_naziv', 'session_key', 'user__email', 'product__naziv')
    readonly_fields = ('dodano', 'azurirano')
    ordering = ('-azurirano',)
    autocomplete_fields = ('user', 'product')


@admin.register(WarehouseLocation)
class WarehouseLocationAdmin(admin.ModelAdmin):
    list_display = ('sifra', 'naziv', 'aktivan', 'redoslijed', 'odoo_location_id')
    list_filter = ('aktivan',)
    search_fields = ('sifra', 'naziv')
    ordering = ('redoslijed', 'sifra')


@admin.register(WarehouseSupplier)
class WarehouseSupplierAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'aktivan')
    search_fields = ('naziv',)


@admin.register(WarehouseCustomer)
class WarehouseCustomerAdmin(admin.ModelAdmin):
    list_display = ('ime_prezime', 'telefon', 'grad', 'adresa', 'azuriran')
    search_fields = ('ime_prezime', 'telefon', 'grad', 'adresa', 'email')
    ordering = ('ime_prezime', 'id')
    readonly_fields = ('kreiran', 'azuriran')


@admin.register(MagacinDeklaracijaBrend)
class MagacinDeklaracijaBrendAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'uvoznik', 'godina_uvoza', 'azuriran')
    search_fields = ('naziv', 'uvoznik', 'adresa', 'telefon')
    ordering = ('naziv', 'id')
    readonly_fields = ('kreiran', 'azuriran')


@admin.register(ProductWarehouseMeta)
class ProductWarehouseMetaAdmin(admin.ModelAdmin):
    list_display = ('product', 'dobavljac', 'min_zaliha', 'jedinica_mjere')
    search_fields = ('product__naziv', 'product__sifra')
    autocomplete_fields = ('product', 'dobavljac')


@admin.register(WarehouseStock)
class WarehouseStockAdmin(admin.ModelAdmin):
    list_display = ('product', 'variation', 'location', 'kolicina', 'rezervisano')
    list_filter = ('location',)
    search_fields = ('product__naziv', 'product__sifra', 'variation__sifra')
    autocomplete_fields = ('product', 'location')


@admin.register(WarehouseMovement)
class WarehouseMovementAdmin(admin.ModelAdmin):
    list_display = ('kreiran', 'product', 'tip', 'location', 'to_location', 'kolicina')
    list_filter = ('tip', 'kreiran')
    search_fields = ('product__naziv', 'product__sifra', 'napomena')
    autocomplete_fields = ('product', 'location', 'to_location', 'korisnik')
    readonly_fields = ('kreiran',)


@admin.register(WarehouseSyncLog)
class WarehouseSyncLogAdmin(admin.ModelAdmin):
    list_display = ('started_at', 'status', 'izvor', 'artikala', 'trajanje_sekundi')
    list_filter = ('status',)
    readonly_fields = (
        'status', 'izvor', 'poruka', 'artikala', 'lokacija',
        'started_at', 'finished_at', 'trajanje_sekundi', 'korisnik',
    )


