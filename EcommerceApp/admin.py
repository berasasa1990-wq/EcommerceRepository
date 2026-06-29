from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .forms import BulkAssignBrandForm, BulkAssignCategoryForm, BulkAssignTagsForm, MergeProductsForm, OdooImportForm
from .odoo_client import OdooClient, OdooError, odoo_je_konfigurisan
from .odoo_import import import_products_from_odoo
from .product_merge import ProductMergeError, merge_products
from .models import (
    Banner,
    Brand,
    Category,
    Coupon,
    LoyaltyCard,
    Order,
    OrderItem,
    Popup,
    Product,
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
        'naziv', 'sifra', 'slika', 'cijena', 'akcijska_cijena',
        'na_stanju', 'stanje', 'redoslijed', 'odoo_template_id', 'pregled_slike',
    )
    readonly_fields = ('odoo_template_id', 'pregled_slike')

    @admin.display(description='Pregled')
    def pregled_slike(self, obj):
        if obj.slika:
            return format_html(
                '<img src="{}" style="height:50px;border-radius:4px;" />',
                obj.slika.url,
            )
        return '—'


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ('pregled_loga',)
    fieldsets = (
        ('Logo', {
            'fields': ('logo', 'pregled_loga'),
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
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Pregled loga (64px visina)')
    def pregled_loga(self, obj):
        if obj.logo:
            return format_html(
                '<img src="{}" style="height:64px;max-width:480px;object-fit:contain;border:1px solid #eee;border-radius:4px;" />',
                obj.logo.url,
            )
        return 'Nema loga — prikazuje se tekstualni logo opremazaribolov.ba. Upload skalira logo i dodaje bijelu pozadinu.'


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
    list_display = ('naziv', 'slug')
    prepopulated_fields = {'slug': ('naziv',)}
    search_fields = ('naziv', 'slug')


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'slug', 'pregled_loga')
    prepopulated_fields = {'slug': ('naziv',)}
    search_fields = ('naziv',)
    readonly_fields = ('pregled_loga_veliki',)
    fields = ('naziv', 'slug', 'slika', 'pregled_loga_veliki')

    @admin.display(description='Logo')
    def pregled_loga(self, obj):
        if obj.slika:
            return format_html(
                '<img src="{}" style="height:24px;max-width:100px;object-fit:contain;" />',
                obj.slika.url,
            )
        return '—'

    @admin.display(description='Pregled loga (200×48)')
    def pregled_loga_veliki(self, obj):
        if obj.slika:
            return format_html(
                '<img src="{}" style="width:200px;height:48px;object-fit:contain;border:1px solid #eee;border-radius:4px;" />',
                obj.slika.url,
            )
        return 'Nema loga — prikazuje se naziv brenda'


@admin.register(Popup)
class PopupAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'aktivan', 'za_prijavljene', 'za_neprijavljene', 'redoslijed')
    list_filter = ('aktivan', 'za_prijavljene', 'za_neprijavljene')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naziv',)
    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (None, {
                'fields': ('naziv', 'slika'),
                'description': 'Unesite interni naziv i dodajte sliku. Dugme će biti ispod slike.',
            }),
            ('Dugme ispod slike', {
                'fields': ('tekst_dugmeta', 'link_dugmeta'),
            }),
            ('Prikaz i ponašanje', {
                'fields': (
                    'aktivan', 'za_prijavljene', 'za_neprijavljene',
                    'redoslijed', 'ponovo_poslije_dana',
                ),
                'classes': ('collapse',),
            }),
        ]
        if obj:
            # Add preview only when editing existing
            fieldsets[0][1]['fields'] = ('naziv', 'slika', 'preview_slika')
        return fieldsets

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['preview_slika']
        return []

    def preview_slika(self, obj):
        from django.utils.html import format_html
        if obj and obj.slika:
            return format_html('<img src="{}" style="max-height:120px; border-radius:6px; margin-top:8px;" />', obj.slika.url)
        return ''
    preview_slika.short_description = 'Pregled'


@admin.register(UpsellOffer)
class UpsellOfferAdmin(admin.ModelAdmin):
    list_display = ('naziv', 'prikaz', 'get_trigger_display', 'popust_postotak', 'aktivan', 'redoslijed')
    list_filter = ('aktivan', 'prikaz')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naziv',)
    filter_horizontal = ('ponuda_artikli',)
    autocomplete_fields = ('trigger_artikal', 'trigger_kategorija')
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
                'Sva polja su opcionalna. '
                'Baner iznad artikala — iznad stavki u korpi. '
                'Baner ispod Nastavi na narudžbu — u korpi ispod checkout dugmeta. '
                'Checkout — poslednja šansa — ispod „Ukupno za plaćanje” na checkout stranici.'
            ),
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


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ('naslov', 'tip', 'aktivan', 'redoslijed', 'pregled_slike')
    list_filter = ('tip', 'aktivan')
    list_editable = ('aktivan', 'redoslijed')
    search_fields = ('naslov', 'podnaslov')
    readonly_fields = ('pregled_slike_velika',)
    fieldsets = (
        ('Sadržaj', {
            'fields': ('naslov', 'podnaslov', 'slika', 'pregled_slike_velika'),
            'description': 'Upload slike bannera bez automatske obrade.',
        }),
        ('Dugmad', {
            'fields': ('tekst_dugmeta', 'link', 'sekundarno_dugme', 'sekundarni_link'),
        }),
        ('Podešavanja', {
            'fields': ('tip', 'siroka_kartica', 'redoslijed', 'aktivan'),
        }),
    )

    @admin.display(description='Slika')
    def pregled_slike(self, obj):
        if obj.slika:
            return format_html(
                '<img src="{}" style="height:40px;border-radius:4px;" />',
                obj.slika.url,
            )
        return '—'

    @admin.display(description='Pregled slike')
    def pregled_slike_velika(self, obj):
        if obj.slika:
            return format_html(
                '<img src="{}" style="max-height:200px;border-radius:8px;" />',
                obj.slika.url,
            )
        return 'Nema slike'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    change_list_template = 'admin/EcommerceApp/product/change_list.html'
    actions = ['bulk_assign_category', 'bulk_assign_brand', 'bulk_assign_tags', 'bulk_merge_products']
    filter_horizontal = ('tagovi',)
    list_display = (
        'naziv', 'sifra', 'brend', 'kategorija', 'cijena',
        'akcijska_cijena', 'na_stanju', 'prikazi_na_pocetnoj', 'aktivan', 'pregled_slike',
    )
    list_filter = ('aktivan', 'na_stanju', 'prikazi_na_pocetnoj', 'kategorija', 'brend', 'tagovi')
    list_editable = ('prikazi_na_pocetnoj', 'aktivan', 'na_stanju')
    search_fields = ('naziv', 'sifra', 'barkod', 'tagovi__naziv', 'odoo_template_id', 'meta_title', 'meta_description')
    prepopulated_fields = {'slug': ('naziv',)}
    readonly_fields = ('pregled_slike_velika', 'odoo_template_id', 'seo_title_preview', 'seo_description_preview')
    inlines = [ProductVariationInline]

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
            'fields': ('naziv', 'slug', 'sifra', 'barkod', 'brend', 'kategorija', 'tagovi', 'opis'),
        }),
        ('Slika i cijena', {
            'fields': ('slika', 'pregled_slike_velika', 'cijena', 'akcijska_cijena', 'akcija_do', 'na_stanju', 'stanje'),
            'description': (
                'Upload slike: uklanja se pozadina, artikal se centrira na bijeloj podlozi 800×800 '
                '(jednake margine), AVIF max 20KB. Isto vrijedi za ručni upload i Odoo import.'
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

    def odoo_import_view(self, request):
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

        if request.method == 'POST':
            form = OdooImportForm(request.POST, odoo_category_choices=odoo_choices)
            if form.is_valid():
                try:
                    stats = import_products_from_odoo(
                        form.cleaned_data['odoo_category_id'],
                        django_category=form.cleaned_data['kategorija'],
                        include_children=form.cleaned_data['ukljuci_podkategorije'],
                        update_existing=form.cleaned_data['azuriraj_postojece'],
                        load_images=form.cleaned_data['ucitaj_slike'],
                        stock_only=form.cleaned_data['samo_stanje'],
                        excluded_brand_ids=[
                            brand.pk for brand in form.cleaned_data['preskoci_brendovi']
                        ],
                    )
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
                except OdooError as exc:
                    messages.error(request, str(exc))
        else:
            form = OdooImportForm(odoo_category_choices=odoo_choices)

        context = {
            **self.admin_site.each_context(request),
            'title': 'Import artikala iz Odoo',
            'form': form,
            'odoo_error': odoo_error,
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

        context = {
            **self.admin_site.each_context(request),
            'title': 'Dodjela tagova',
            'form': form,
            'form_field': form['tagovi'],
            'queryset': queryset,
            'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'action_name': 'bulk_assign_tags',
            'submit_label': 'Dodaj tagove',
        }
        return render(request, 'admin/EcommerceApp/product/bulk_assign_field.html', context)

    bulk_assign_tags.short_description = 'Dodaj tagove'

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
        if obj.slika:
            return format_html(
                '<img src="{}" style="height:40px;border-radius:4px;" />',
                obj.slika.url,
            )
        return '—'

    @admin.display(description='Pregled slike')
    def pregled_slike_velika(self, obj):
        if obj.slika:
            return format_html(
                '<img src="{}" style="max-height:200px;border-radius:8px;" />',
                obj.slika.url,
            )
        return 'Nema slike'

    @admin.display(description='Automatski SEO naslov')
    def seo_title_preview(self, obj):
        if obj:
            return format_html(
                '<div style="padding:8px 12px; background:#f8f9fa; border:1px solid #ddd; border-radius:4px; font-size:13px; margin:2px 0;">'
                '<strong>Koristiće se ako polje ostane prazno:</strong><br>'
                '<span style="color:#0a66c2; font-weight:500;">{}</span>'
                '</div>',
                obj.seo_title
            )
        return '—'

    @admin.display(description='Automatski meta opis')
    def seo_description_preview(self, obj):
        if obj:
            return format_html(
                '<div style="padding:8px 12px; background:#f8f9fa; border:1px solid #ddd; border-radius:4px; font-size:13px; line-height:1.4; margin:2px 0;">'
                '<strong>Koristiće se ako polje ostane prazno:</strong><br>'
                '<span style="color:#0a66c2;">{}</span>'
                '</div>',
                obj.seo_description
            )
        return '—'