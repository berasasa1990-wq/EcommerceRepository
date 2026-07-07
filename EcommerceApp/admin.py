import logging

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, mark_safe
from django.middleware.csrf import get_token

from .forms import (
    AkcijaAdminForm,
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
    LiveVisitor,
    Akcija,
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


@admin.register(Akcija)
class AkcijaAdmin(admin.ModelAdmin):
    form = AkcijaAdminForm
    list_display = ('naziv', 'tip', 'artikal', 'gratis_artikal', 'popust_postotak', 'gratis_popup', 'aktivan', 'redoslijed')
    list_filter = ('tip', 'aktivan')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naziv', 'artikal__naziv', 'gratis_artikal__naziv', 'kategorija__naziv')
    autocomplete_fields = ('artikal', 'gratis_artikal', 'kategorija')
    readonly_fields = ('preview_slika',)

    class Media:
        js = ('admin/js/akcija_admin.js',)

    fieldsets = (
        (None, {
            'fields': ('naziv', 'tip', 'aktivan', 'redoslijed'),
            'description': (
                '1) Pop-up + slika — upload slike, tekst/link dugmeta, boje, kašnjenje; prikazuje se dok je uključeno (bez trajanja i %). '
                '2) Akcija + tajmer — artikal, % sniženja, odbrojavanje. '
                '3) X+1 — samo u korpi (1+1 / 2+1 / 3+1). '
                '4) Uslov prodaja — prag se računa od cijele korpe minus 1 komad ovog artikla; popust na 1 komad. '
                '5) Korpa nudjenje — artikal + % + kategorija; pored stavki iz kategorije u korpi. '
                '6) + Gratis — trigger + drugi artikal sa % popusta; opcionalno pop-up (oba u korpu na klik). '
                'Sve akcije rade dok je „Aktivan” uključen.'
            ),
        }),
        ('Sadržaj', {
            'fields': (
                'slika', 'preview_slika',
                'artikal', 'gratis_artikal', 'popust_postotak', 'gratis_popup',
                'kategorija', 'prag_korpe_km', 'deal_vrsta',
                'pocetak', 'trajanje_sati',
                'tekst_dugmeta', 'link_dugmeta',
                'boja_dugmeta', 'boja_opisa',
            ),
        }),
        ('Pop-up ponašanje', {
            'fields': (
                'popup_delay_seconds', 'za_prijavljene', 'za_neprijavljene',
                'ponovo_poslije_dana',
            ),
            'description': (
                'Za prikaz pop-upa uključite obje opcije publike ako želite da svi vide akciju. '
                'Akcija radi dok je „Aktivan” uključen. Početak/trajanje služi samo za odbrojavanje u pop-upu. '
                '„Ponovo prikaži poslije (dana)” = 0 znači ponovo u svakoj novoj posjeti; 7 = pauza 7 dana nakon zatvaranja.'
            ),
            'classes': ('collapse',),
        }),
    )

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


@admin.register(LiveVisitor)
class LiveVisitorAdmin(admin.ModelAdmin):
    list_display = ('ime', 'email', 'user', 'last_seen', 'first_seen', 'session_key')
    list_filter = ('last_seen',)
    search_fields = ('ime', 'email', 'session_key', 'user__email')
    readonly_fields = ('first_seen', 'last_seen', 'session_key')
    ordering = ('-last_seen',)


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


