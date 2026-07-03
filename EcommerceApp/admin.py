import logging

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.middleware.csrf import get_token

from .forms import (
    BannerAdminForm,
    BulkAssignBrandForm,
    BulkAssignCategoryForm,
    BulkAssignTagsForm,
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
    Banner,
    Brand,
    Category,
    ChatConversation,
    ChatMessage,
    Coupon,
    HomeFeaturedProduct,
    HomeVlog,
    LoyaltyCard,
    Order,
    OrderItem,
    Popup,
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
    readonly_fields = ('product_naziv', 'varijacija_naziv', 'sifra', 'cijena', 'kolicina')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('broj', 'korisnik', 'ime_prezime', 'email', 'telefon', 'ukupno', 'status', 'kreirana')
    list_filter = ('status', 'kreirana')
    search_fields = ('broj', 'ime_prezime', 'email', 'telefon', 'korisnik__email')
    readonly_fields = ('broj', 'kreirana', 'medjuzbir', 'dostava', 'popust', 'ukupno')
    autocomplete_fields = ('korisnik',)
    inlines = [OrderItemInline]
    fieldsets = (
        ('Narudžba', {'fields': ('broj', 'status', 'medjuzbir', 'popust', 'kupon_kod', 'dostava', 'ukupno', 'kreirana')}),
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
        'naziv', 'sifra', 'slika', 'cijena', 'akcija_postotak', 'akcijska_cijena',
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


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ('pregled_loga', 'pregled_favicona', 'pregled_badgea')
    inlines = [HomeFeaturedProductInline]
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
            'description': 'Naslovi sekcija Novo, Izdvojeno i Blog. Prazno polje = naslov se ne prikazuje.',
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


@admin.register(Popup)
class PopupAdmin(admin.ModelAdmin):
    form = PopupAdminForm
    list_display = ('naziv', 'tip', 'aktivan', 'za_prijavljene', 'za_neprijavljene', 'redoslijed')
    list_filter = ('tip', 'aktivan', 'za_prijavljene', 'za_neprijavljene')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naziv',)
    autocomplete_fields = ('akcija_artikal',)

    def get_fieldsets(self, request, obj=None):
        sadrzaj_fields = [
            'slika',
            'akcija_pocetak', 'akcija_sati', 'akcija_artikal',
            'akcija_popust_postotak', 'akcija_prag_iznos',
            'tekst_dugmeta', 'link_dugmeta',
            'boja_dugmeta', 'boja_akcija_istice',
        ]
        if obj:
            sadrzaj_fields = ['slika', 'preview_slika', *sadrzaj_fields[1:]]
        return [
            (None, {
                'fields': ('naziv', 'tip'),
            }),
            ('Sadržaj pop-upa', {
                'fields': tuple(sadrzaj_fields),
                'description': (
                    'Slika + dugme: upload slike i link dugmeta. '
                    'Akcijski pop-up: početak akcije, trajanje u satima i artikal ispod tajmera. '
                    'Za uslovni popust: unesite % popusta na artikal i prag iznosa u korpi (npr. 50 KM) da bi se popust primijenio na taj artikal.'
                ),
            }),
            ('Prikaz i ponašanje', {
                'fields': (
                    'aktivan', 'za_prijavljene', 'za_neprijavljene',
                    'redoslijed', 'ponovo_poslije_dana', 'popup_delay_seconds',
                ),
                'classes': ('collapse',),
            }),
        ]

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['preview_slika']
        return []

    @admin.display(description='Pregled slike')
    def preview_slika(self, obj):
        if obj and obj.slika:
            return format_html(
                '<img src="{}" style="max-height:120px; border-radius:6px; margin-top:8px;" />',
                obj.slika.url,
            )
        return ''


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
        ('X+1 Količinski deal (1+1 / 2+1 / 3+1)', {
            'fields': ('deal_artikal', 'deal_vrsta', 'deal_popust'),
            'description': (
                'Odaberite artikal. Izaberite vrstu (npr. 2+1). Unesite % popusta na +1 artikal (100=GRATIS). '
                'Kada kupac doda artikal u korpu, ispod količine će se pojaviti crvena poruka. '
                'Ako dostigne količinu,  +1 artikal će biti snižen za taj %. '
                'Npr. 2+1 + 50% → kada uzme 3, treći plaća 50% cijene.'
            ),
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
    actions = [
        'bulk_assign_category', 'bulk_assign_brand', 'bulk_assign_tags',
        'bulk_proizvedeno_u_japanu', 'bulk_ukloni_japan', 'bulk_merge_products',
    ]
    filter_horizontal = ('tagovi',)
    list_display = (
        'naziv', 'sifra', 'brend', 'kategorija', 'cijena',
        'akcijska_cijena', 'na_stanju', 'prikazi_na_pocetnoj', 'aktivan', 'pregled_slike',
    )
    list_filter = (
        'aktivan', NaStanjuFilter, 'prikazi_na_pocetnoj', 'proizvedeno_u_japanu',
        'kategorija', 'brend', 'tagovi',
    )
    list_editable = ('prikazi_na_pocetnoj', 'aktivan', 'na_stanju')
    search_fields = (
        'naziv', 'sifra', 'barkod', 'tagovi__naziv',
        'kategorija__naziv', 'kategorija__roditelj__naziv',
        'odoo_template_id', 'meta_title', 'meta_description',
    )
    prepopulated_fields = {'slug': ('naziv',)}
    readonly_fields = ('pregled_slike_velika', 'odoo_template_id', 'seo_title_preview', 'seo_description_preview')
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
                'slika', 'pregled_slike_velika', 'cijena',
                'akcija_postotak', 'akcijska_cijena', 'akcija_do',
                'na_stanju', 'stanje',
            ),
            'description': (
                'Akcija: unesite popust (%) za automatski izračun akcijske cijene, '
                'ili ručno unesite akcijsku cijenu. Upload slike: AVIF max 15KB + responsive 120/200/320w.'
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
        ('Odoo', {
            'fields': ('odoo_template_id',),
            'classes': ('collapse',),
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'import-odoo/',
                self.admin_site.admin_view(self.odoo_import_view),
                name='EcommerceApp_product_odoo_import',
            ),
        ]
        return custom_urls + urls

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

    def bulk_assign_category(self, request, queryset):
        if 'apply' in request.POST:
            form = BulkAssignCategoryForm(request.POST)
            if form.is_valid():
                selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
                products = Product.objects.filter(pk__in=selected_ids)
                category = form.cleaned_data['kategorija']
                count = products.update(kategorija=category)
                self.message_user(
                    request,
                    f'{count} artikal/a dodijeljeno kategoriji „{category}”.',
                    messages.SUCCESS,
                )
                return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))
        else:
            form = BulkAssignCategoryForm()

        context = {
            **self.admin_site.each_context(request),
            'title': 'Dodjela kategorije',
            'form': form,
            'queryset': queryset,
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
        form = BulkAssignTagsForm(request.POST or None)
        if request.method == 'POST' and 'apply' in request.POST and form.is_valid():
            selected_ids = request.POST.getlist(helpers.ACTION_CHECKBOX_NAME)
            products = Product.objects.filter(pk__in=selected_ids)
            tags = form.cleaned_data['tagovi']
            count = 0
            for product in products:
                product.tagovi.add(*tags)
                count += 1
            tag_names = ', '.join(tag.naziv for tag in tags)
            self.message_user(
                request,
                f'Tagovi ({tag_names}) dodani na {count} artikal/a.',
                messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse('admin:EcommerceApp_product_changelist'))

        # Group tags hierarchically for easier bulk assignment (main tag + all descendants)
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
            for d in descendants:
                all_covered_pks.add(d.pk)

        # Flat tags that are not part of any hierarchy
        flat_tags = list(
            Tag.objects.exclude(pk__in=all_covered_pks).order_by('naziv')
        )

        context = {
            **self.admin_site.each_context(request),
            'title': 'Dodjela tagova',
            'form': form,
            'grouped_tags': grouped_tags,
            'flat_tags': flat_tags,
            'queryset': queryset,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_tags',
            'submit_label': 'Dodaj tagove',
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