import logging

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db.models import Q
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
from .product_merge import ProductMergeError, merge_products
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
    HomeCategoryShowcase,
    HomeFeaturedProduct,
    HomeVlog,
    LoyaltyCard,
    Order,
    OrderItem,
    Popup,
    OnlineGiftCampaign,
    OnlineGiftClaim,
    OnlineGiftPush,
    Product,
    ProductImage,
    ProductVariation,
    SiteSettings,
    Tag,
    UpsellOffer,
    UserProfile,
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
    list_display = ('broj', 'korisnik', 'ime_prezime', 'email', 'telefon', 'ukupno', 'status', 'kreirana')
    list_filter = ('status', 'kreirana')
    search_fields = ('broj', 'ime_prezime', 'email', 'telefon', 'korisnik__email')
    readonly_fields = ('broj', 'kreirana', 'medjuzbir', 'dostava', 'popust', 'ukupno', 'popust_detalji')
    autocomplete_fields = ('korisnik',)
    inlines = [OrderItemInline]
    fieldsets = (
        ('Narudžba', {
            'fields': (
                'broj', 'status', 'medjuzbir', 'popust', 'kupon_kod',
                'popust_detalji', 'dostava', 'ukupno', 'kreirana',
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


class HomeFeaturedProductInline(admin.TabularInline):
    model = HomeFeaturedProduct
    fk_name = 'postavke'
    extra = 0
    max_num = 10
    autocomplete_fields = ('artikal',)
    fields = ('artikal', 'redoslijed', 'aktivan')
    verbose_name = 'Istaknuti artikal'
    verbose_name_plural = 'Istaknuti artikli na početnoj (do 10, prikaz 6 + slide)'

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['artikal'].help_text = (
            'Pretražite i odaberite postojeći artikal — ne kreirajte novi.'
        )
        return formset


class HomeCategoryShowcaseInline(admin.TabularInline):
    model = HomeCategoryShowcase
    fk_name = 'postavke'
    extra = 1
    autocomplete_fields = ('kategorija',)
    fields = ('kategorija', 'naslov', 'redoslijed', 'aktivan')
    verbose_name = 'Kategorija (2×2)'
    verbose_name_plural = (
        'Kategorije na početnoj (ispod Izdvojenih) — 4 artikla, 2×2 na mobilnom'
    )

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.form.base_fields['kategorija'].help_text = (
            'Odaberite kategoriju čiji se artikli prikazuju u mreži 2×2 na mobilnom.'
        )
        formset.form.base_fields['naslov'].help_text = (
            'Opcionalno. Prazno = naziv kategorije.'
        )
        return formset


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ('pregled_loga', 'pregled_favicona', 'pregled_badgea')
    inlines = [HomeFeaturedProductInline, HomeCategoryShowcaseInline]
    fieldsets = (
        ('Logo i ikona', {
            'fields': ('logo', 'pregled_loga', 'favicon', 'pregled_favicona'),
            'description': 'Logo u headeru i ikona u tabu preglednika (favicon).',
        }),
        ('Kontakt', {
            'fields': ('kontakt_telefon', 'kontakt_messenger'),
            'description': 'Telefon za WhatsApp/Viber i Facebook stranica za Messenger u donjem desnom uglu.',
        }),
        ('Dostava', {
            'fields': ('dostava_naziv', 'dostava_cijena', 'besplatna_dostava_od'),
            'description': 'Postavke dostave prikazane u korpi i na checkoutu.',
        }),
        ('Exit popup (cijeli sajt)', {
            'fields': (
                'korpa_exit_popup_aktivan',
                'korpa_exit_popup_artikal',
                'korpa_exit_popup_popust',
            ),
            'description': (
                '„Poslednji minut” — samo kad kupac hoće da izađe (kursor prema zatvaranju taba). '
                'Artikal se bira automatski: 1) skoro dodao u korpu (hover bez klika), '
                '2) prema gledanju, 3) artikal ispod kao fallback. Popust % na cijenu u popupu.'
            ),
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
        }),
        ('Pogodnosti', {
            'fields': (
                'novi_korisnik_besplatna_dostava',
                'novi_korisnik_popust_postotak',
                'novi_korisnik_popust_km',
            ),
            'description': 'Pogodnosti za registrovane korisnike na prvoj narudžbi. Popust u % i KM se mogu kombinovati.',
        }),
        ('SEO (Google i društvene mreže)', {
            'fields': ('seo_title', 'meta_description', 'og_image'),
            'description': 'Naslov i opis za Google pretragu i kad se link dijeli (Facebook, WhatsApp, itd.). '
                           'Og image treba biti široka slika (preporučeno 1200×630 px).',
        }),
        ('Početna stranica — tekstovi', {
            'fields': (
                'naslov_novo', 'podnaslov_novo',
                'naslov_izdvojeno', 'podnaslov_izdvojeno',
                'naslov_blog',
            ),
            'description': (
                'Naslovi sekcija Novo, Izdvojeno i Blog. Prazno polje = naslov se ne prikazuje. '
                'Na mobilnom se naslovi Novo/Izdvojeno ne prikazuju. Kategorije 2×2 dodajte u inline ispod.'
            ),
        }),
        ('Stranica artikla — povezani artikli', {
            'fields': ('naslov_povezani', 'podnaslov_povezani'),
            'description': 'Naslov karusela povezanih artikala. U podnaslovu koristite {kategorija} za naziv kategorije.',
        }),
        ('Stranica artikla — badge i uslovi', {
            'fields': ('badge_product_detail', 'pregled_badgea', 'politika_dostava', 'politika_povrat', 'politika_garancija'),
            'description': 'Badge se prikazuje u gornjem lijevom uglu slike artikla (npr. garancija). Tekstovi ispod dugmeta „Dodaj u korpu”.',
        }),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Pregled loga (64px visina)')
    def pregled_loga(self, obj):
        if obj and obj.logo:
            return format_html(
                '<img src="{}" style="height:64px;max-width:480px;object-fit:contain;border:1px solid #eee;border-radius:4px;" />',
                obj.logo.url,
            )
        return 'Nema loga — prikazuje se tekstualni logo opremazaribolov.ba. Upload skalira logo i dodaje bijelu pozadinu.'

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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'roditelj', 'nivo_prikaz', 'meta_title', 'redoslijed', 'prikazi_u_meniju', 'aktivan')
    list_filter = ('aktivan', 'prikazi_u_meniju', 'roditelj')
    list_editable = ('redoslijed', 'prikazi_u_meniju', 'aktivan')
    prepopulated_fields = {'slug': ('naziv',)}
    search_fields = ('naziv', 'slug', 'meta_title', 'meta_description')
    autocomplete_fields = ('roditelj',)
    fieldsets = (
        ('Osnovno', {
            'fields': ('naziv', 'slug', 'roditelj'),
            'description': 'Ostavite roditelja praznog za glavnu kategoriju u meniju (npr. Men, Women). '
                           'Za podkategoriju izaberite roditelja. Za sub-podkategoriju izaberite podkategoriju kao roditelja.',
        }),
        ('Prikaz', {
            'fields': ('redoslijed', 'prikazi_u_meniju', 'aktivan'),
        }),
        ('SEO (Google i društvene mreže)', {
            'fields': ('meta_title', 'meta_description'),
            'description': 'Prilagođeni naslov i opis za ovu kategoriju u Google pretrazi. '
                           'Ako ostaviš prazno, koristi se naziv kategorije + default opis.',
        }),
        ('Odoo', {
            'fields': ('odoo_category_id',),
            'classes': ('collapse',),
            'description': 'ID Odoo product.category za automatsko mapiranje pri importu.',
        }),
    )

    @admin.display(description='Nivo')
    def nivo_prikaz(self, obj):
        levels = ['Glavna', 'Podkategorija', 'Sub-podkategorija']
        return levels[min(obj.nivo, 2)]


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
    list_display = ('naziv', 'slug', 'pregled_loga')
    prepopulated_fields = {'slug': ('naziv',)}
    search_fields = ('naziv',)
    readonly_fields = ('pregled_loga_veliki',)
    fields = ('naziv', 'slug', 'slika', 'pregled_loga_veliki')

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


class AkcijaBundleLineInline(admin.TabularInline):
    model = AkcijaBundleLine
    extra = 2
    min_num = 0
    autocomplete_fields = ('product',)
    fields = ('product', 'quantity', 'popust_postotak', 'redoslijed')
    ordering = ('redoslijed', 'id')
    verbose_name = 'Stavka seta'
    verbose_name_plural = (
        'BUNDLE SET — artikal + količina (+ opcionalno % samo za taj artikal). '
        'Količina 2 = jedna slika ×2 u popup-u. '
        'Prazan % na liniji = koristi % kompletnog seta.'
    )
    classes = ('akcija-inline-bundle-lines',)

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
    """Po artiklu unesi svoj flash popust %."""
    model = ProductDwellItem
    fk_name = 'settings'
    extra = 1
    autocomplete_fields = ('product',)
    fields = ('product', 'popust')
    verbose_name = 'Artikal s popustom'
    verbose_name_plural = (
        'AI dwell artikli — dodaj artikal i unesi popust % (npr. 8, 12, 20)'
    )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'product':
            from .models import Product
            kwargs['queryset'] = Product.objects.filter(aktivan=True).order_by('naziv')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(AIProdajaSettings)
class AIProdajaSettingsAdmin(admin.ModelAdmin):
    """AI prodaja — u adminu pored Akcija (ne u općim Podešavanjima)."""
    filter_horizontal = ('product_dwell_artikli',)
    inlines = [ProductDwellItemInline]
    fieldsets = (
        ('AI prodaja (popup)', {
            'fields': (
                'browse_interest_popup_aktivan',
                'browse_interest_popust',
            ),
            'description': (
                'AI prati kupca i šalje do 2 popup-a (1–2 artikla), razmak ~3 min, max 10%.'
            ),
        }),
        ('AI dwell (flash cijena na artiklu)', {
            'fields': (
                'product_dwell_popup_aktivan',
                'product_dwell_popust',
            ),
            'description': (
                'Odmah na ulasku na artikal — BEZ popupa; precrtana + snizena + 2 min. '
                'Ispod dodaj artikle i za svaki upiši svoj popust %. '
                'Ako nema nijednog u tabeli — default popust na SVIM artiklima '
                '(ili stara lista ako još postoji).'
            ),
        }),
        ('AI dwell — stara lista (opcionalno)', {
            'classes': ('collapse',),
            'fields': ('product_dwell_artikli',),
            'description': (
                'Koristi samo ako još nisi prebacio na tabelu s popustom po artiklu. '
                'Kad ima unosa u tabeli ispod, stara lista se ignorira.'
            ),
        }),
    )

    def has_add_permission(self, request):
        return not AIProdajaSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = AIProdajaSettings.load()
        return redirect(
            reverse('admin:EcommerceApp_aiprodajasettings_change', args=[obj.pk]),
        )


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
        'naziv', 'tip', 'artikal', 'popust_postotak',
        'bundle_trigger', 'aktivan', 'redoslijed',
    )
    list_filter = ('tip', 'aktivan', 'bundle_trigger')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naziv', 'artikal__naziv', 'kategorija__naziv')
    autocomplete_fields = ('artikal', 'kategorija')
    filter_horizontal = ('bundle_artikli',)
    # Samo bundle inline — količinski popusti su polja na formi (2, 3, 4…)
    inlines = [AkcijaBundleLineInline]

    class Media:
        js = ('admin/js/akcija_admin.js',)

    fieldsets = (
        (None, {
            'fields': ('naziv', 'tip', 'aktivan', 'redoslijed'),
            'description': (
                'Samo 2 tipa: „Pop-up bundle” i „Kupi više (količinski %)”. '
                'AI prodaju podesi u meniju „AI prodaja”.'
            ),
        }),
        ('Sadržaj', {
            'fields': (
                'bundle_trigger',
                'popust_postotak',
                'artikal',
                'kategorija',
                'tekst_dugmeta',
                'boja_dugmeta',
                'boja_opisa',
            ),
        }),
        ('Kupi više — količina i popust', {
            'fields': (
                'qty_2_popust',
                'qty_3_popust',
                'qty_4_popust',
                'qty_5_popust',
                'qty_6_popust',
            ),
            'description': (
                'Samo za tip „Kupi više”. '
                'Upiši npr. 10 pored „Kupi 2 komada” (= -10% za 2 kom), '
                '20 pored „Kupi 3 komada” (= -20% za 3 kom). '
                'Prazno polje = ta opcija se ne nudi.'
            ),
        }),
        ('Pop-up ponašanje', {
            'fields': (
                'popup_delay_seconds', 'za_prijavljene', 'za_neprijavljene',
                'ponovo_poslije_dana',
            ),
            'description': (
                'Kašnjenje i publika. Za „Kupi više” popup se najbolje vidi na stranici artikla.'
            ),
        }),
        ('Legacy M2M (opcionalno)', {
            'classes': ('collapse',),
            'fields': ('bundle_artikli',),
            'description': 'Stari način (bez količine). Preferiraj inline stavke iznad.',
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(tip__in=Akcija.ACTIVE_TIPS)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == 'tip':
            kwargs['choices'] = [
                (Akcija.Tip.BUNDLE, Akcija.Tip.BUNDLE.label),
                (Akcija.Tip.QTY_DEAL, Akcija.Tip.QTY_DEAL.label),
            ]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if obj.tip not in Akcija.ACTIVE_TIPS:
            obj.tip = Akcija.Tip.BUNDLE
        super().save_model(request, obj, form, change)
        if hasattr(form, 'save_qty_deal_tiers'):
            form.save_qty_deal_tiers(obj)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        obj = form.instance
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
    list_display = ('naslov', 'aktivan', 'redoslijed', 'pregled_slike')
    list_filter = ('aktivan',)
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naslov', 'slug', 'sadrzaj')
    prepopulated_fields = {'slug': ('naslov',)}
    readonly_fields = ('pregled_slike_velika',)
    fieldsets = (
        (None, {
            'fields': ('naslov', 'slika', 'pregled_slike_velika', 'sadrzaj'),
            'description': (
                'Vlogovi se prikazuju na početnoj ispod Izdvojeno (3 u redu). '
                'Upload slike: konvertuje se u AVIF (max 30KB). Klik otvara stranicu s opisom.'
            ),
        }),
        ('Podešavanja', {
            'fields': ('slug', 'redoslijed', 'aktivan'),
            'classes': ('collapse',),
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
    readonly_fields = ('pregled_slike_velika', 'pregled_videa')
    fieldsets = (
        ('Sadržaj', {
            'fields': ('naslov', 'podnaslov', 'slika', 'pregled_slike_velika', 'video', 'pregled_videa'),
            'description': (
                'Klik na banner vodi na kategoriju ili link (ako su postavljeni). '
                'Obavezna je slika ili video. '
                'Upload slike: Hero → JPEG 1920×560 (24:7), Grid/Featured/Spotlight → AVIF ili JPEG. '
                'Video: MP4/WebM/MOV, najviše 6 sekundi (max 20 MB). Ako je video postavljen, prikazuje se umjesto slike; '
                'slika može služiti kao poster kad je video aktivan. '
                'Tip „Hero Carousel” za karusel, „Grid Kartica” za 8 kartica ispod (4×2 desktop, 6 mobilni).'
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

    @admin.display(description='Pregled slike')
    def pregled_slike_velika(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="max-height:200px;border-radius:8px;" />',
                obj.slika.url,
            )
        return 'Nema slike'

    @admin.display(description='Pregled videa')
    def pregled_videa(self, obj):
        if obj and obj.video:
            return format_html(
                '<video src="{}" style="max-height:200px;border-radius:8px;" controls muted playsinline></video>',
                obj.video.url,
            )
        return 'Nema videa'


class NaStanjuFilter(admin.SimpleListFilter):
    """Custom filter for 'na_stanju' that defaults to 'Yes' (in stock) selected."""
    title = 'Na stanju'
    parameter_name = 'na_stanju'

    def lookups(self, request, model_admin):
        return (
            ('1', 'Da'),
            ('0', 'Ne'),
        )

    def value(self):
        # Default to 'Yes' (1) if no value provided in query
        val = super().value()
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
        'bulk_proizvedeno_u_japanu', 'bulk_ukloni_japan', 'bulk_merge_products',
        'bulk_objavi_na_olx_pik',
    ]
    filter_horizontal = ('tagovi',)
    list_display = (
        'naziv', 'sifra', 'brend', 'kategorija', 'cijena',
        'akcijska_cijena', 'na_stanju', 'prikazi_na_pocetnoj', 'aktivan',
        'datum_dodavanja', 'olx_status', 'pregled_slike',
    )
    list_filter = (
        'aktivan', NaStanjuFilter, 'prikazi_na_pocetnoj', 'proizvedeno_u_japanu',
        'kategorija', 'brend', 'tagovi',
        ('kreiran', admin.DateFieldListFilter),
    )
    date_hierarchy = 'kreiran'
    ordering = ('-kreiran',)
    list_editable = ('prikazi_na_pocetnoj', 'aktivan', 'na_stanju')
    search_fields = (
        'naziv', 'sifra', 'barkod', 'tagovi__naziv',
        'kategorija__naziv', 'kategorija__roditelj__naziv',
        'odoo_template_id', 'meta_title', 'meta_description',
    )

    def get_search_results(self, request, queryset, search_term):
        """
        Autocomplete za početničke setove: samo artikli na stanju.
        (model_name=advisorbeginnersetitem u /admin/autocomplete/)
        """
        queryset, use_distinct = super().get_search_results(
            request, queryset, search_term,
        )
        model_name = (request.GET.get('model_name') or '').lower()
        if model_name == 'advisorbeginnersetitem':
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
            'fields': ('prikazi_na_pocetnoj', 'aktivan'),
        }),
        ('SEO (Google i društvene mreže)', {
            'fields': (
                'seo_title_preview', 'meta_title',
                'seo_description_preview', 'meta_description',
            ),
            'description': 'Polja ispod služe samo za <strong>ručno preklapanje</strong>. '
                           'Ako ih ostaviš prazna, sistem automatski koristi naziv artikla + opis ispod.',
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
            excluded_brand_ids=options['excluded_brand_ids'],
            client=client,
            template_ids=template_ids,
            start=start,
            limit=import_chunk_size(
                load_images=options['load_images'],
                stock_only=options['stock_only'],
                images_only=options.get('images_only', False),
            ),
        )
        stats = merge_import_stats(stats, chunk_stats)
        job['position'] = stats['position']
        job['stats'] = stats
        return job, stats

    def _finish_import_success(self, request, stats):
        request.session.pop(ODOO_IMPORT_SESSION_KEY, None)
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
                    return self._finish_import_success(request, stats)

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
                        return self._finish_import_success(request, stats)

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
        queryset = queryset.select_related('kategorija')
        categories = [
            {'id': category.pk, 'label': str(category)}
            for category in Category.objects.filter(aktivan=True).select_related(
                'roditelj', 'roditelj__roditelj',
            ).order_by('redoslijed', 'naziv')
        ]

        if 'apply' in request.POST:
            selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            count = 0
            skipped = 0
            for pk_str in selected_ids:
                try:
                    pk = int(pk_str)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                category_id = (request.POST.get(f'kategorija_{pk}') or '').strip()
                if not category_id:
                    skipped += 1
                    continue
                try:
                    category = Category.objects.get(pk=int(category_id), aktivan=True)
                except (Category.DoesNotExist, TypeError, ValueError):
                    skipped += 1
                    continue
                if Product.objects.filter(pk=pk).update(kategorija=category):
                    count += 1
                else:
                    skipped += 1

            if count:
                self.message_user(
                    request,
                    f'Kategorija dodijeljena na {count} artikal/a.',
                    messages.SUCCESS,
                )
            if skipped:
                self.message_user(
                    request,
                    f'{skipped} artikal/a preskočeno (nije odabrana kategorija).',
                    messages.WARNING,
                )
            if not count and not skipped:
                self.message_user(
                    request,
                    'Nije odabrana nijedna kategorija.',
                    messages.ERROR,
                )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Dodjela kategorije',
            'queryset': queryset,
            'categories': categories,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_category',
        }
        return render(request, 'admin/EcommerceApp/product/bulk_assign_category.html', context)

    bulk_assign_category.short_description = 'Dodaj u postojeću kategoriju'

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

    def bulk_assign_tags(self, request, queryset):
        queryset = queryset.prefetch_related('tagovi')
        grouped_tags, flat_tags = self._bulk_tag_groups()

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
                tag_ids = [
                    int(tag_id)
                    for tag_id in request.POST.getlist(f'tagovi_{pk}')
                    if str(tag_id).isdigit()
                ]
                if not tag_ids:
                    skipped += 1
                    continue
                try:
                    product = Product.objects.get(pk=pk)
                except Product.DoesNotExist:
                    skipped += 1
                    continue
                tags = list(Tag.objects.filter(pk__in=tag_ids))
                if not tags:
                    skipped += 1
                    continue
                product.tagovi.add(*tags)
                count += 1

            if count:
                self.message_user(
                    request,
                    f'Tagovi dodani na {count} artikal/a.',
                    messages.SUCCESS,
                )
            if skipped:
                self.message_user(
                    request,
                    f'{skipped} artikal/a preskočeno (nije odabran nijedan tag).',
                    messages.WARNING,
                )
            if not count and not skipped:
                self.message_user(
                    request,
                    'Nije odabran nijedan tag.',
                    messages.ERROR,
                )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Dodjela tagova',
            'grouped_tags': grouped_tags,
            'flat_tags': flat_tags,
            'queryset': queryset,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_tags',
            'submit_label': 'Primjeni tagove',
        }
        return render(request, 'admin/EcommerceApp/product/bulk_assign_tags.html', context)

    bulk_assign_tags.short_description = 'Dodaj tagove'

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


