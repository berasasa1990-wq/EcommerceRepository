from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


def _akcija_jos_vazi(akcija_do):
    if akcija_do is None:
        return True
    return akcija_do >= timezone.localdate()


def _izracunaj_akcijsku_od_postotka(bazna_cijena, postotak):
    if bazna_cijena is None or postotak is None or postotak <= 0:
        return None
    faktor = Decimal('1') - (postotak / Decimal('100'))
    return (bazna_cijena * faktor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _izracunaj_postotak_umanjenja(bazna_cijena, prikazna_cijena):
    if (
        bazna_cijena is None
        or prikazna_cijena is None
        or bazna_cijena <= 0
        or prikazna_cijena >= bazna_cijena
    ):
        return None
    postotak = (
        (bazna_cijena - prikazna_cijena) / bazna_cijena * Decimal('100')
    ).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    if postotak <= 0:
        return None
    return int(postotak)


class SiteSettings(models.Model):
    class ArtikalaPoRedu(models.IntegerChoices):
        TRI = 3, '3 artikla u redu'
        CETIRI = 4, '4 artikla u redu'

    logo = models.ImageField(
        upload_to='site/', blank=True, null=True,
        verbose_name='Logo sajta',
        help_text='Prikazuje se u headeru umjesto teksta. Čuva se kao PNG s bijelom pozadinom (max 640×128px).',
    )
    favicon = models.ImageField(
        upload_to='site/', blank=True, null=True,
        verbose_name='Ikona sajta (favicon)',
        help_text='Prikazuje se u tabu preglednika i kao prečica na mobilnom. Automatski se skalira na 32×32px PNG.',
    )
    dostava_naziv = models.CharField(
        max_length=100,
        default='xExpress Brza Pošta',
        verbose_name='Naziv dostave',
    )
    dostava_cijena = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('11.00'),
        verbose_name='Cijena dostave (KM)',
    )
    besplatna_dostava_od = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('250.00'),
        verbose_name='Besplatna dostava od (KM)',
        help_text='Narudžbe iznad ovog iznosa imaju besplatnu dostavu.',
    )
    korpa_exit_popup_aktivan = models.BooleanField(
        default=False,
        verbose_name='Exit popup aktivan',
        help_text='Prikazuje popup na cijelom sajtu kad posjetilac pomjeri kursor prema zatvaranju taba.',
    )
    korpa_exit_popup_naslov = models.CharField(
        max_length=120,
        default='Prije nego odete…',
        blank=True,
        verbose_name='Korpa — exit popup naslov',
    )
    korpa_exit_popup_tekst = models.TextField(
        blank=True,
        default='Završite narudžbu sada — artikli u korpi čekaju na vas.',
        verbose_name='Korpa — exit popup tekst',
    )
    korpa_exit_popup_artikal = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='korpa_exit_popupi',
        verbose_name='Korpa — exit popup artikal',
        help_text='Opcionalno. Prikazuje se u popupu s dugmetom za dodavanje u korpu.',
    )
    korpa_exit_popup_popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Exit popup popust (%)',
        help_text='Opcionalno. Smanjuje cijenu odabranog artikla pri dodavanju iz popupa (max 50%).',
    )
    browse_interest_popup_aktivan = models.BooleanField(
        default=True,
        verbose_name='Personalizovana ponuda (gledanje) aktivna',
        help_text=(
            'Automatski popup do 4 artikla 2×2 („Specijalna ponuda za vas”): '
            'nakon 2 min na sajtu, prema onome što kupac najviše gleda (jednom).'
        ),
    )
    browse_interest_popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        null=True,
        blank=True,
        verbose_name='Personalizovana ponuda — popust (%)',
        help_text='Popust na preporučene artikle (max 50%). Podrazumijevano 10%.',
    )
    product_dwell_popup_aktivan = models.BooleanField(
        default=False,
        verbose_name='Popust na artikal (1 min gledanja) aktivan',
        help_text=(
            'Uključeno: ako kupac gleda isti artikal duže od 1 minute, '
            'iskače popup s popustom na taj artikal (jednom po artiklu).'
        ),
    )
    product_dwell_popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        null=True,
        blank=True,
        verbose_name='Popust na artikal (1 min) — %',
        help_text='Popust na artikal kad se gleda ≥ 1 min (max 50%). Podrazumijevano 10%.',
    )
    welcome_reg_popup_aktivan = models.BooleanField(
        default=False,
        verbose_name='Registracija + popust (odmah) aktivan',
        help_text=(
            'Uključeno: gostu na početku (nakon nekoliko sekundi) iskače poziv '
            '„Registruj se” s popustom na prvu narudžbu.'
        ),
    )
    welcome_reg_popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        null=True,
        blank=True,
        verbose_name='Registracija — popust na prvu narudžbu (%)',
        help_text='Npr. 10 = 10% kupon nakon registracije (max 50%).',
    )
    welcome_reg_delay_seconds = models.PositiveSmallIntegerField(
        default=8,
        verbose_name='Registracija popup — prikaži nakon (sekundi)',
        help_text='Koliko sekundi nakon ulaska na sajt (0 = odmah).',
    )
    online_nagrada_bočni_aktivan = models.BooleanField(
        default=False,
        verbose_name='Nagradna igra (bočni popup) aktivna',
        help_text=(
            'Uključeno: nagradna igra se nudi kao mali pulsirajući popup sa strane '
            '(ne preko cijelog ekrana). Igranje i dalje zahtijeva registraciju. '
            'Mora postojati aktivna kampanja Online nagrada u adminu.'
        ),
    )
    online_nagrada_delay_seconds = models.PositiveSmallIntegerField(
        default=15,
        verbose_name='Nagradna igra — prikaži nakon (sekundi)',
        help_text='Kašnjenje bočnog popupa nagradne igre (0 = odmah).',
    )
    novi_korisnik_besplatna_dostava = models.BooleanField(
        default=False,
        verbose_name='Novi korisnici — besplatna dostava',
        help_text='Primjenjuje se na prvu narudžbu registrovanog korisnika.',
    )
    novi_korisnik_popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Novi korisnici — popust (%)',
        help_text='Opcionalno. Npr. unesite 10 za 10% popusta na prvu narudžbu.',
    )
    novi_korisnik_popust_km = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Novi korisnici — popust (KM)',
        help_text='Opcionalno. Fiksni iznos popusta na prvu narudžbu.',
    )
    artikala_po_redu = models.PositiveSmallIntegerField(
        choices=ArtikalaPoRedu.choices,
        default=ArtikalaPoRedu.CETIRI,
        verbose_name='Artikala u redu (katalog)',
        help_text='Broj artikala u jednom redu na početnoj i stranicama kategorija. Po stranici se prikazuje 4 reda.',
    )
    prikazi_filter_na_pocetnoj = models.BooleanField(
        default=False,
        verbose_name='Prikaži filter na početnoj',
        help_text='Uključuje filter sidebar lijevo od artikala na početnoj stranici.',
    )
    seo_title = models.CharField(
        max_length=70, blank=True,
        verbose_name='SEO naslov (početna)',
        help_text='Preporučeno 50-60 znakova. Ostavi prazno za default.',
    )
    meta_description = models.CharField(
        max_length=160, blank=True,
        verbose_name='Meta opis (početna)',
        help_text='Preporučeno do 155-160 znakova. Prikazuje se u Google rezultatima.',
    )
    og_image = models.ImageField(
        upload_to='site/', blank=True, null=True,
        verbose_name='Social share slika (OG image)',
        help_text='Preporučeno 1200×630px ili veća. Prikazuje se kad se link dijeli na Facebooku, WhatsAppu itd.',
    )
    politika_dostava = models.TextField(
        default='Dostava brzom poštom u roku od 48h.',
        verbose_name='Uslovi dostave — tekst',
    )
    politika_povrat = models.TextField(
        default='Ukoliko je roba oštećena ili ne odgovara poručenoj, vršimo povrat.',
        verbose_name='Povrat robe — tekst',
    )
    politika_garancija = models.TextField(
        default='Garancija na kvalitet.',
        verbose_name='Garancija — tekst',
    )
    badge_product_detail = models.ImageField(
        upload_to='site/', blank=True, null=True,
        verbose_name='Badge na slici artikla',
        help_text='Prikazuje se u gornjem lijevom uglu slike na stranici artikla (npr. garancija). PNG s transparentnom pozadinom.',
    )
    naslov_novo = models.CharField(
        max_length=120, default='Novo', blank=True,
        verbose_name='Novo — naslov',
        help_text='Ostavite prazno da se naslov ne prikazuje.',
    )
    podnaslov_novo = models.CharField(
        max_length=200, default='Najnoviji artikli na sajtu', blank=True,
        verbose_name='Novo — podnaslov',
        help_text='Ostavite prazno da se podnaslov ne prikazuje.',
    )
    naslov_izdvojeno = models.CharField(
        max_length=120, default='Izdvojeno', blank=True,
        verbose_name='Izdvojeno — naslov',
        help_text='Ostavite prazno da se naslov ne prikazuje.',
    )
    podnaslov_izdvojeno = models.CharField(
        max_length=200, default='Odabrani artikli za vas', blank=True,
        verbose_name='Izdvojeno — podnaslov',
        help_text='Ostavite prazno da se podnaslov ne prikazuje.',
    )
    naslov_povezani = models.CharField(
        max_length=120, default='Povezani artikli', blank=True,
        verbose_name='Povezani artikli — naslov',
        help_text='Na stranici artikla. Ostavite prazno da se naslov ne prikazuje.',
    )
    podnaslov_povezani = models.CharField(
        max_length=200, default='Iz kategorije {kategorija}', blank=True,
        verbose_name='Povezani artikli — podnaslov',
        help_text='Koristite {kategorija} za naziv kategorije. Ostavite prazno da se podnaslov ne prikazuje.',
    )
    naslov_blog = models.CharField(
        max_length=200, default='Blogovi — Klik na željeni',
        verbose_name='Blog — naslov',
    )
    kontakt_telefon = models.CharField(
        max_length=30, blank=True,
        verbose_name='Kontakt telefon (WhatsApp / Viber)',
        help_text='Broj za WhatsApp i Viber ikone (npr. +387 61 123 456). Prazno = koristi STORE_PHONE iz okruženja.',
    )
    kontakt_messenger = models.CharField(
        max_length=120, blank=True,
        verbose_name='Facebook Messenger',
        help_text='Korisničko ime Facebook stranice za Messenger, npr. opremazaribolov.ba',
    )

    class Meta:
        verbose_name = 'Podešavanja'
        verbose_name_plural = 'Podešavanja'

    def save(self, *args, **kwargs):
        self.pk = 1
        from .utils.images import (
            apply_image_processing,
            process_product_detail_badge,
            process_site_favicon,
            process_site_logo,
        )

        if self.logo:
            apply_image_processing(self, 'logo', post_process=process_site_logo)
        if self.favicon:
            apply_image_processing(self, 'favicon', post_process=process_site_favicon)
        if self.badge_product_detail:
            apply_image_processing(self, 'badge_product_detail', post_process=process_product_detail_badge)
        super().save(*args, **kwargs)

    def format_povezani_podnaslov(self, kategorija_naziv=''):
        if not self.podnaslov_povezani:
            return ''
        return self.podnaslov_povezani.replace('{kategorija}', kategorija_naziv or '')

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def artikala_po_stranici(self):
        return self.artikala_po_redu * 4

    def __str__(self):
        return 'Podešavanja'


class Category(models.Model):
    naziv = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank=True)
    roditelj = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True,
        related_name='podkategorije', verbose_name='Roditeljska kategorija',
    )
    redoslijed = models.PositiveIntegerField(default=0)
    prikazi_u_meniju = models.BooleanField(default=True, verbose_name='Prikaži u meniju')
    aktivan = models.BooleanField(default=True)
    odoo_category_id = models.PositiveIntegerField(
        blank=True, null=True, unique=True, verbose_name='Odoo category ID',
    )
    meta_title = models.CharField(
        max_length=70, blank=True,
        verbose_name='SEO naslov',
        help_text='Opcionalno. Ako ostaviš prazno koristi se naziv kategorije.',
    )
    meta_description = models.CharField(
        max_length=160, blank=True,
        verbose_name='Meta opis',
        help_text='Opcionalno. Kratak opis za Google i društvene mreže.',
    )

    class Meta:
        verbose_name = 'Kategorija'
        verbose_name_plural = 'Kategorije'
        ordering = ['redoslijed', 'naziv']

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.naziv)
            if self.roditelj:
                base_slug = f'{self.roditelj.slug}-{base_slug}'
            slug = base_slug
            counter = 1
            while Category.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base_slug}-{counter}'
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('category', kwargs={'slug': self.slug})

    @property
    def nivo(self):
        level = 0
        parent = self.roditelj
        while parent:
            level += 1
            parent = parent.roditelj
        return level

    def get_descendant_ids(self):
        ids = [self.pk]
        for child in self.podkategorije.all():
            if child.aktivan:
                ids.extend(child.get_descendant_ids())
        return ids

    def __str__(self):
        if self.roditelj:
            return f'{self.roditelj.naziv} → {self.naziv}'
        return self.naziv


class Tag(models.Model):
    naziv = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    roditelj = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='podtagovi',
        verbose_name='Glavni tag (roditelj)',
        help_text='Ako je izabran, ovaj tag je podtag glavnog taga (npr. "Masinice" kao glavni, a "Shimano", "Daiwa" pod njim).',
    )

    class Meta:
        verbose_name = 'Tag'
        verbose_name_plural = 'Tagovi'
        ordering = ['roditelj__naziv', 'naziv']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.naziv)
        super().save(*args, **kwargs)

    def __str__(self):
        if self.roditelj:
            return f'{self.roditelj.naziv} → {self.naziv}'
        return self.naziv

    def get_all_descendants(self, include_self=False):
        """Return all sub-tags recursively (for bulk/group assignment)."""
        descendants = set()
        if include_self:
            descendants.add(self)
        for child in self.podtagovi.all():
            descendants.add(child)
            descendants.update(child.get_all_descendants(include_self=True))
        return descendants


class Brand(models.Model):
    naziv = models.CharField(max_length=100)
    slug = models.SlugField(unique=True, blank=True)
    slika = models.ImageField(
        upload_to='brands/', blank=True, null=True,
        verbose_name='Logo slika',
        help_text='Prikazuje se umjesto naziva. Automatski se skalira na 200×48px (logo popunjava 80% prostora).',
    )

    class Meta:
        verbose_name = 'Brend'
        verbose_name_plural = 'Brendovi'
        ordering = ['naziv']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.naziv)
        if self.slika:
            from .utils.images import apply_image_processing, process_brand_logo
            apply_image_processing(self, 'slika', post_process=process_brand_logo)
        super().save(*args, **kwargs)

    @property
    def prikazi_logo(self):
        return bool(self.slika)

    def __str__(self):
        return self.naziv


class Banner(models.Model):
    class BannerType(models.TextChoices):
        HERO = 'hero', 'Hero Carousel'
        GRID = 'grid', 'Grid Kartica (4×2 ispod Hero, 8 desktop / 6 mobilni)'
        FEATURED = 'featured', 'Featured Kartica'
        SPOTLIGHT = 'spotlight', 'Spotlight'

    naslov = models.CharField(max_length=200, blank=True, default='')
    podnaslov = models.CharField(max_length=300, blank=True)
    slika = models.ImageField(upload_to='banners/', blank=True, null=True)
    video = models.FileField(
        upload_to='banners/videos/',
        blank=True,
        null=True,
        verbose_name='Video (max 6 s)',
        help_text='Opcionalno. MP4/WebM/MOV, najviše 6 sekundi. Ako je postavljen, prikazuje se umjesto slike.',
    )
    kategorija = models.ForeignKey(
        'Category',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='banneri',
        verbose_name='Kategorija',
        help_text='Opcionalno. Ako nema linka, klik vodi na ovu kategoriju (uz filter cijene).',
        limit_choices_to={'aktivan': True},
    )
    link = models.CharField(
        max_length=300, blank=True,
        verbose_name='Link',
        help_text='Opcionalno. Puni URL ili putanja. Ako je prazno, koristi se kategorija iznad.',
    )
    filter_cijena_do = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Filter: do cijene (KM)',
        help_text='Opcionalno. Npr. 50 = samo artikli ≤ 50 KM iz odabrane kategorije.',
    )
    filter_cijena_od = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Filter: od cijene (KM)',
        help_text='Opcionalno. Npr. 50 = samo artikli ≥ 50 KM iz odabrane kategorije.',
    )
    tekst_dugmeta = models.CharField(max_length=50, blank=True, default='')
    sekundarno_dugme = models.CharField(max_length=50, blank=True)
    sekundarni_link = models.CharField(max_length=300, blank=True)
    tip = models.CharField(max_length=20, choices=BannerType.choices, default=BannerType.HERO)
    siroka_kartica = models.BooleanField(default=False, help_text='Samo za Featured tip')
    redoslijed = models.PositiveIntegerField(default=0)
    aktivan = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Banner'
        verbose_name_plural = 'Banneri'
        ordering = ['redoslijed', '-id']

    @property
    def ima_medij(self):
        return bool(self.slika) or bool(self.video)

    def save(self, *args, **kwargs):
        if self.video:
            from django.core.exceptions import ValidationError
            from .utils.videos import validate_banner_video
            try:
                validate_banner_video(self.video)
            except ValidationError as exc:
                raise ValueError(exc.messages[0]) from exc
        if self.slika:
            from functools import partial
            from .utils.images import apply_image_processing, process_banner_image_for_admin
            apply_image_processing(
                self,
                'slika',
                post_process=partial(process_banner_image_for_admin, tip=self.tip),
            )
        super().save(*args, **kwargs)

    def get_link_href(self):
        href = None
        if self.link:
            if self.link.startswith(('http://', 'https://', '/')):
                href = self.link
            else:
                href = f'/{self.link.strip("/")}/'
        elif self.kategorija_id:
            href = self.kategorija.get_absolute_url()
        if not href:
            return None
        return self._append_price_filter_to_href(href)

    @staticmethod
    def _decimal_query_value(value):
        if value is None:
            return None
        normalized = format(value, 'f')
        if '.' in normalized:
            normalized = normalized.rstrip('0').rstrip('.')
        return normalized or '0'

    def _append_price_filter_to_href(self, href):
        if href.startswith(('http://', 'https://')):
            return href

        from urllib.parse import parse_qsl, urlencode

        filter_params = {}
        cijena_do = self._decimal_query_value(self.filter_cijena_do)
        cijena_od = self._decimal_query_value(self.filter_cijena_od)
        if cijena_do is not None:
            filter_params['cijena_do'] = cijena_do
        if cijena_od is not None:
            filter_params['cijena_od'] = cijena_od
        if not filter_params:
            return href

        base, fragment = (href.split('#', 1) + [''])[:2]
        path, _, existing_query = base.partition('?')
        params = dict(parse_qsl(existing_query, keep_blank_values=True))
        params.update(filter_params)

        result = path
        query = urlencode(params)
        if query:
            result = f'{result}?{query}'

        if not fragment and path.rstrip('/') in ('', '/'):
            fragment = 'product-showcase'
        if fragment:
            result = f'{result}#{fragment}'
        return result

    def __str__(self):
        label = self.naslov or f'Banner #{self.pk}' if self.pk else 'Banner'
        return f'{self.get_tip_display()} — {label}'


class HomeFeaturedProduct(models.Model):
    postavke = models.ForeignKey(
        SiteSettings,
        on_delete=models.CASCADE,
        related_name='istaknuti_artikli',
        default=1,
        editable=False,
    )
    artikal = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        related_name='istaknuti_na_pocetnoj',
        verbose_name='Postojeći artikal',
        limit_choices_to={'aktivan': True},
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Istaknuti artikal (početna)'
        verbose_name_plural = 'Istaknuti artikli (početna)'
        ordering = ['redoslijed', 'id']

    def __str__(self):
        return self.artikal.naziv


class HomeCategoryShowcase(models.Model):
    postavke = models.ForeignKey(
        SiteSettings,
        on_delete=models.CASCADE,
        related_name='kategorije_na_pocetnoj',
        default=1,
        editable=False,
    )
    kategorija = models.ForeignKey(
        'Category',
        on_delete=models.CASCADE,
        related_name='pocetna_sekcije',
        verbose_name='Kategorija',
        limit_choices_to={'aktivan': True},
    )
    naslov = models.CharField(
        max_length=120,
        blank=True,
        verbose_name='Naslov sekcije',
        help_text='Prazno = naziv kategorije.',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Kategorija na početnoj (2×2)'
        verbose_name_plural = 'Kategorije na početnoj (2×2 mobil)'
        ordering = ['redoslijed', 'id']

    def display_title(self):
        return (self.naslov or '').strip() or self.kategorija.naziv

    def __str__(self):
        return self.display_title()


class HomeVlog(models.Model):
    naslov = models.CharField(
        max_length=200,
        verbose_name='Naziv',
        help_text='Prikazuje se ispod slike na početnoj.',
    )
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    slika = models.ImageField(
        upload_to='vlogs/',
        verbose_name='Slika',
        help_text='Upload: AVIF max 18KB + responsive 180/280/360w. Prikaz na početnoj (3 u redu) i stranici vloga.',
    )
    sadrzaj = models.TextField(
        verbose_name='Opis vloga',
        help_text='Puni tekst koji se prikazuje kad korisnik otvori vlog. Može HTML: <p>, <a href="/...">link</a>.',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Vlog'
        verbose_name_plural = 'Vlogovi'
        ordering = ['redoslijed', '-id']

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.naslov) or 'vlog'
            slug = base_slug
            counter = 1
            while HomeVlog.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base_slug}-{counter}'
                counter += 1
            self.slug = slug
        if self.slika:
            from .utils.images import apply_image_processing, process_vlog_image
            apply_image_processing(self, 'slika', post_process=process_vlog_image)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('vlog_detail', kwargs={'slug': self.slug})

    def __str__(self):
        return self.naslov


class Akcija(models.Model):
    class Tip(models.TextChoices):
        SLIKA = 'slika', 'Pop-up + slika'
        TIMER = 'timer', 'Akcija + tajmer'
        X_PLUS_1 = 'x_plus_1', 'X+1 prodaja (samo korpa)'
        USLOV = 'uslov', 'Uslov prodaja'
        KORPA_NUDJENJE = 'korpa_nudjenje', 'Korpa nudjenje'
        GRATIS = 'gratis', '+ Gratis'
        BUNDLE = 'bundle', 'Pop-up bundle'

    class BundleTrigger(models.TextChoices):
        DELAY = 'delay', 'Nakon kašnjenja (bilo gdje na sajtu)'
        BUNDLE_PRODUCT = 'bundle_product', 'Kad gleda artikal iz seta'
        TRIGGER_PRODUCT = 'trigger_product', 'Kad gleda odabrani trigger artikal'
        CATEGORY = 'category', 'Kad gleda odabranu kategoriju'

    naziv = models.CharField(
        max_length=100,
        verbose_name='Interni naziv',
        help_text='Samo za prepoznavanje u adminu.',
    )
    tip = models.CharField(
        max_length=16,
        choices=Tip.choices,
        default=Tip.SLIKA,
        verbose_name='Tip akcije',
    )
    slika = models.ImageField(
        upload_to='akcije/',
        blank=True,
        null=True,
        verbose_name='Slika',
        help_text='Obavezno za tip „Pop-up + akcija + slika”.',
    )
    artikal = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='akcije',
        verbose_name='Artikal',
        help_text=(
            'Aktivan artikal (tajmer, uslov, X+1, korpa nudjenje, + Gratis trigger). '
            'Za Pop-up bundle: samo ako je trigger „odabrani trigger artikal”.'
        ),
    )
    gratis_artikal = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='akcije_gratis',
        verbose_name='Gratis artikal',
        help_text='Za + Gratis: drugi artikal s popustom (%).',
    )
    bundle_artikli = models.ManyToManyField(
        'Product',
        blank=True,
        related_name='akcije_bundle',
        verbose_name='Artikli u setu (bundle)',
        help_text='Za Pop-up bundle: 2 ili više artikala. % popusta vrijedi za kompletan set.',
    )
    bundle_trigger = models.CharField(
        max_length=20,
        choices=BundleTrigger.choices,
        default=BundleTrigger.DELAY,
        blank=True,
        verbose_name='Šta trigeruje pop-up bundle',
        help_text='Kada se bundle prikaže. Samo tada iskače.',
    )
    gratis_popup = models.BooleanField(
        default=False,
        verbose_name='Prikaži kao pop-up',
        help_text='Uključeno = ponuda u pop-upu (oba artikla na klik). Isključeno = automatski u korpi pri dodavanju trigger artikla.',
    )
    kategorija = models.ForeignKey(
        'Category',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='akcije_korpa_nudjenje',
        verbose_name='Kategorija (trigger)',
        help_text=(
            'Korpa nudjenje: artikli iz kategorije vide ponudu. '
            'Pop-up bundle: ako je trigger „kategorija”.'
        ),
    )
    popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust (%)',
        help_text='Za Pop-up bundle: % sniženja na kompletan set (svaki artikal u setu).',
    )
    prag_korpe_km = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Uslov: iznos u korpi (KM)',
        help_text='Prag u KM — u iznos se računa cijela korpa minus tačno 1 komad ovog artikla.',
    )
    deal_vrsta = models.CharField(
        max_length=10,
        choices=[
            ('1+1', '1+1 (kupi 1, drugi snižen)'),
            ('2+1', '2+1 (kupi 2, treći snižen)'),
            ('3+1', '3+1 (kupi 3, četvrti snižen)'),
        ],
        blank=True,
        null=True,
        verbose_name='Vrsta X+1',
    )
    pocetak = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Početak akcije',
    )
    trajanje_sati = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        verbose_name='Trajanje akcije (sati)',
    )
    tekst_dugmeta = models.CharField(
        max_length=50,
        default='Saznaj više',
        verbose_name='Tekst dugmeta',
    )
    link_dugmeta = models.CharField(
        max_length=300,
        blank=True,
        verbose_name='Link dugmeta',
        help_text='Prazno = stranica artikla ili /registracija/.',
    )
    boja_dugmeta = models.CharField(
        max_length=7,
        default='#5BB805',
        verbose_name='Boja dugmeta',
    )
    boja_opisa = models.CharField(
        max_length=7,
        default='#5BB805',
        verbose_name='Boja opisa',
        help_text='Boja teksta opisa / tajmera / poruke.',
    )
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')
    za_prijavljene = models.BooleanField(
        default=True,
        verbose_name='Prikaži prijavljenim korisnicima',
    )
    za_neprijavljene = models.BooleanField(
        default=True,
        verbose_name='Prikaži neprijavljenim korisnicima',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    ponovo_poslije_dana = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Ponovo prikaži poslije (dana)',
        help_text='0 = ponovo u svakoj novoj posjeti (novi prozor). Npr. 7 = ne prikazuj 7 dana nakon zatvaranja.',
    )
    popup_delay_seconds = models.PositiveSmallIntegerField(
        default=5,
        verbose_name='Prikaži pop-up nakon (sekundi)',
        help_text='0 = odmah. Ne vrijedi za X+1 (samo korpa).',
    )

    class Meta:
        verbose_name = 'Akcija'
        verbose_name_plural = 'Akcije'
        ordering = ['redoslijed', '-id']

    @property
    def zavrsava(self):
        if not self.pocetak or not self.trajanje_sati:
            return None
        from datetime import timedelta

        pocetak = self.pocetak
        if timezone.is_naive(pocetak):
            pocetak = timezone.make_aware(pocetak, timezone.get_current_timezone())
        return pocetak + timedelta(hours=self.trajanje_sati)

    def jos_traje(self):
        """Akcija vrijedi dok je uključena u adminu (Aktivan = da)."""
        return self.aktivan

    def je_popup(self):
        if self.tip == self.Tip.GRATIS:
            return bool(self.gratis_popup)
        if self.tip == self.Tip.BUNDLE:
            return True
        return self.tip in {self.Tip.SLIKA, self.Tip.TIMER, self.Tip.USLOV}

    def bundle_products(self):
        """Aktivni artikli u setu."""
        if self.tip != self.Tip.BUNDLE:
            return []
        return list(
            self.bundle_artikli.filter(aktivan=True).order_by('naziv', 'id')
        )

    def bundle_display_items(self):
        """Lista {product, bazna, snizena} za template popupa."""
        items = []
        for p in self.bundle_products():
            cijene = self.bundle_cijene_za_artikal(p)
            bazna = cijene['bazna'] if cijene else p.prikazna_cijena
            snizena = cijene['snizena'] if cijene else p.prikazna_cijena
            items.append({
                'product': p,
                'bazna': bazna,
                'snizena': snizena,
                'has_discount': bool(cijene and cijene['snizena'] < cijene['bazna']),
                'usteda': (bazna - snizena) if cijene and snizena < bazna else Decimal('0'),
            })
        return items

    def bundle_pricing_summary(self):
        """
        Ukupno „inače” vs cijena seta — za vizuelnu usporedbu
        (precrtana suma pojedinačnih cijena vs zelena cijena seta).
        """
        items = self.bundle_display_items()
        if len(items) < 2:
            return None
        total_bazna = sum((i['bazna'] or Decimal('0')) for i in items)
        total_snizena = sum((i['snizena'] or Decimal('0')) for i in items)
        usteda = total_bazna - total_snizena
        if usteda < 0:
            usteda = Decimal('0')
        pct = self.popust_postotak
        pct_label = ''
        if pct is not None:
            pct_label = str(int(pct)) if pct == int(pct) else str(pct)
        return {
            'count': len(items),
            'total_bazna': total_bazna.quantize(Decimal('0.01')),
            'total_snizena': total_snizena.quantize(Decimal('0.01')),
            'usteda': usteda.quantize(Decimal('0.01')),
            'pct_label': pct_label,
            'has_discount': usteda > 0,
        }

    def _category_matches_root(self, category, root_id):
        """True ako je category root ili potomek root-a (bilo koji nivo)."""
        seen = set()
        while category is not None and category.pk not in seen:
            if category.pk == root_id:
                return True
            seen.add(category.pk)
            category = getattr(category, 'roditelj', None)
        return False

    def bundle_trigger_matches(self, request):
        """Da li trenutna stranica zadovoljava trigger bundle popupa."""
        if self.tip != self.Tip.BUNDLE or not request:
            return False
        trigger = (self.bundle_trigger or self.BundleTrigger.DELAY).strip()
        path = (getattr(request, 'path', '') or '').rstrip('/') or '/'

        if trigger == self.BundleTrigger.DELAY or trigger == '':
            return True

        import re

        if trigger == self.BundleTrigger.BUNDLE_PRODUCT:
            m = re.match(r'^/artikal/([^/]+)$', path)
            if not m:
                return False
            return self.bundle_artikli.filter(slug=m.group(1), aktivan=True).exists()

        if trigger == self.BundleTrigger.TRIGGER_PRODUCT:
            if not self.artikal_id:
                return False
            m = re.match(r'^/artikal/([^/]+)$', path)
            if not m:
                return False
            return bool(
                self.artikal
                and self.artikal.slug == m.group(1)
                and self.artikal.aktivan
            )

        if trigger == self.BundleTrigger.CATEGORY:
            # SAMO na trigger kategoriji (stranica kategorije ili artikal u toj grani)
            if not self.kategorija_id:
                return False
            from .models import Category, Product

            m = re.match(r'^/kategorija/([^/]+)$', path)
            if m:
                page_cat = (
                    Category.objects.filter(slug=m.group(1), aktivan=True)
                    .select_related('roditelj')
                    .first()
                )
                return bool(
                    page_cat and self._category_matches_root(page_cat, self.kategorija_id)
                )

            m = re.match(r'^/artikal/([^/]+)$', path)
            if m:
                p = (
                    Product.objects.filter(slug=m.group(1), aktivan=True)
                    .select_related('kategorija', 'kategorija__roditelj')
                    .first()
                )
                if not p or not p.kategorija_id:
                    return False
                return self._category_matches_root(p.kategorija, self.kategorija_id)

            # Ni kategorija ni artikal — ne prikazuj
            return False

        return False

    def prikazi_korisniku(self, user, request=None):
        if not self.jos_traje():
            return False
        if self.tip == self.Tip.GRATIS:
            if not self.gratis_popup:
                return False
            if not self.artikal_id or not self.gratis_artikal_id or self.popust_postotak is None:
                return False
        elif self.tip == self.Tip.BUNDLE:
            if self.popust_postotak is None:
                return False
            if self.pk and self.bundle_artikli.filter(aktivan=True).count() < 2:
                return False
            # Trigger je obavezan — bez request-a ne prikazuj (osim delay)
            trigger = (self.bundle_trigger or self.BundleTrigger.DELAY).strip()
            if trigger != self.BundleTrigger.DELAY and request is None:
                return False
            if request is not None and not self.bundle_trigger_matches(request):
                return False
            if trigger == self.BundleTrigger.CATEGORY and not self.kategorija_id:
                return False
            if trigger == self.BundleTrigger.TRIGGER_PRODUCT and not self.artikal_id:
                return False
        elif self.tip in {self.Tip.X_PLUS_1, self.Tip.KORPA_NUDJENJE}:
            return False
        if self.tip == self.Tip.SLIKA and not self.slika:
            return False
        if self.tip in {self.Tip.TIMER, self.Tip.USLOV} and not self.artikal_id:
            return False
        if user.is_authenticated:
            return bool(self.za_prijavljene)
        return bool(self.za_neprijavljene)

    def korpa_nudjenje_snizena_cijena(self, product, variation=None):
        """Snižena cijena za Korpa nudjenje (% od trenutne prikazne cijene)."""
        if (
            self.tip != self.Tip.KORPA_NUDJENJE
            or not self.popust_postotak
            or not self.jos_traje()
            or not self.artikal_id
            or product.pk != self.artikal_id
        ):
            return None
        bazna = variation.prikazna_cijena if variation else product.prikazna_cijena
        return _izracunaj_akcijsku_od_postotka(bazna, self.popust_postotak)

    def timer_snizena_cijena(self, product, variation=None):
        """Snižena cijena za Akcija + tajmer (% od trenutne prikazne cijene)."""
        if (
            self.tip != self.Tip.TIMER
            or not self.popust_postotak
            or not self.jos_traje()
            or not self.artikal_id
            or product.pk != self.artikal_id
        ):
            return None
        bazna = variation.prikazna_cijena if variation else product.prikazna_cijena
        return _izracunaj_akcijsku_od_postotka(bazna, self.popust_postotak)

    def gratis_snizena_cijena(self, product, variation=None):
        """Snižena cijena za + Gratis (% od trenutne prikazne cijene drugog artikla)."""
        if (
            self.tip != self.Tip.GRATIS
            or self.popust_postotak is None
            or not self.jos_traje()
            or not self.gratis_artikal_id
            or product.pk != self.gratis_artikal_id
        ):
            return None
        bazna = variation.prikazna_cijena if variation else product.prikazna_cijena
        return _izracunaj_akcijsku_od_postotka(bazna, self.popust_postotak)

    def gratis_cijene_za_prikaz(self):
        """Originalna i snižena cijena gratis artikla za pop-up."""
        if self.tip != self.Tip.GRATIS or not self.gratis_artikal_id or self.popust_postotak is None:
            return None
        artikal = self.gratis_artikal
        if artikal is None:
            return None
        bazna = artikal.prikazna_cijena
        snizena = _izracunaj_akcijsku_od_postotka(bazna, self.popust_postotak)
        if snizena is None:
            return None
        return {
            'bazna': bazna,
            'snizena': snizena,
            'pct': self.popust_postotak,
        }

    def timer_cijene_za_prikaz(self):
        """Originalna i snižena cijena za pop-up tajmera."""
        if self.tip != self.Tip.TIMER or not self.artikal_id or not self.popust_postotak:
            return None
        artikal = self.artikal
        if artikal is None:
            return None
        bazna = artikal.prikazna_cijena
        snizena = _izracunaj_akcijsku_od_postotka(bazna, self.popust_postotak)
        if snizena is None or snizena >= bazna:
            return None
        return {
            'bazna': bazna,
            'snizena': snizena,
            'pct': self.popust_postotak,
        }

    def bundle_cijene_za_artikal(self, product):
        """Originalna i snižena cijena artikla u Pop-up bundle setu."""
        if self.tip != self.Tip.BUNDLE or not product or self.popust_postotak is None:
            return None
        if self.pk and not self.bundle_artikli.filter(pk=product.pk).exists():
            return None
        bazna = product.prikazna_cijena
        snizena = _izracunaj_akcijsku_od_postotka(bazna, self.popust_postotak)
        if snizena is None:
            return None
        return {
            'bazna': bazna,
            'snizena': snizena,
            'pct': self.popust_postotak,
        }

    def get_link_href(self):
        if self.artikal_id and self.tip in {self.Tip.TIMER, self.Tip.USLOV}:
            return self.artikal.get_absolute_url()
        if self.link_dugmeta:
            if self.link_dugmeta.startswith(('http://', 'https://', '/')):
                return self.link_dugmeta
            return f'/{self.link_dugmeta.strip("/")}/'
        return reverse('register')

    def __str__(self):
        status = 'aktivan' if self.aktivan else 'neaktivan'
        return f'{self.naziv} ({self.get_tip_display()}, {status})'


class Popup(models.Model):
    class Tip(models.TextChoices):
        SLIKA = 'slika', 'Slika + dugme'
        AKCIJA = 'akcija', 'Akcijski pop-up (tajmer + artikal)'

    naziv = models.CharField(
        max_length=100,
        verbose_name='Interni naziv',
        help_text='Samo za prepoznavanje u adminu.',
    )
    tip = models.CharField(
        max_length=10,
        choices=Tip.choices,
        default=Tip.SLIKA,
        verbose_name='Tip pop-upa',
    )
    slika = models.ImageField(
        upload_to='popups/',
        blank=True,
        null=True,
        verbose_name='Slika',
        help_text='Glavna slika pop-upa. Dugme će biti ispod slike. Obavezno za tip „Slika + dugme”.',
    )
    akcija_sati = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        verbose_name='Trajanje akcije (sati)',
        help_text='Koliko sati traje odbrojavanje od početka akcije.',
    )
    akcija_pocetak = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name='Početak akcije',
        help_text='Od kada se računa odbrojavanje (početak + sati = kraj akcije).',
    )
    akcija_artikal = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='akcija_popupi',
        verbose_name='Artikal u akciji',
        help_text='Prikazuje se ispod tajmera u akcijskom pop-upu.',
    )
    akcija_popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='% popusta na artikal',
        help_text='Popust koji se primjenjuje na artikal ako je ukupno u korpi preko praga (samo za tip AKCIJA).',
    )
    akcija_prag_iznos = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Prag ukupne kupovine (KM)',
        help_text='Minimalni iznos u korpi da bi se popust na artikal primijenio. Npr. 50. (samo za tip AKCIJA)',
    )
    tekst_dugmeta = models.CharField(
        max_length=50,
        default='Saznaj više',
        verbose_name='Naziv dugmeta',
        help_text='Tekst koji se prikazuje na dugmetu ispod slike.',
    )
    link_dugmeta = models.CharField(
        max_length=300,
        blank=True,
        verbose_name='Link dugmeta',
        help_text='Npr. /registracija/ ili puni URL. Prazno = /registracija/.',
    )
    boja_dugmeta = models.CharField(
        max_length=7,
        default='#5BB805',
        verbose_name='Boja dugmeta',
        help_text='Hex boja za pozadinu dugmeta u pop-upu (npr. #5BB805).',
    )
    boja_akcija_istice = models.CharField(
        max_length=7,
        default='#5BB805',
        verbose_name='Boja "Akcija ističe za"',
        help_text='Hex boja za labelu "Akcija ističe za" i slične elemente u akcijskom pop-upu.',
    )
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')
    za_prijavljene = models.BooleanField(
        default=False,
        verbose_name='Prikaži prijavljenim korisnicima',
    )
    za_neprijavljene = models.BooleanField(
        default=True,
        verbose_name='Prikaži neprijavljenim korisnicima',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    ponovo_poslije_dana = models.PositiveSmallIntegerField(
        default=7,
        verbose_name='Ponovo prikaži poslije (dana)',
        help_text='Koliko dana ne prikazivati nakon što korisnik zatvori pop-up.',
    )
    popup_delay_seconds = models.PositiveSmallIntegerField(
        default=5,
        verbose_name='Prikaži pop-up nakon (sekundi)',
        help_text='Koliko sekundi nakon učitavanja stranice da se prikaže pop-up (0 = odmah).',
    )

    class Meta:
        verbose_name = 'Pop-up'
        verbose_name_plural = 'Pop-upi'
        ordering = ['redoslijed', '-id']

    @property
    def akcija_zavrsava(self):
        if self.tip != self.Tip.AKCIJA or not self.akcija_pocetak or not self.akcija_sati:
            return None
        from datetime import timedelta

        from django.utils import timezone

        pocetak = self.akcija_pocetak
        if timezone.is_naive(pocetak):
            pocetak = timezone.make_aware(pocetak, timezone.get_current_timezone())
        return pocetak + timedelta(hours=self.akcija_sati)

    def akcija_jos_traje(self):
        from django.utils import timezone

        kraj = self.akcija_zavrsava
        if not kraj:
            return False
        return timezone.now() < kraj

    def prikazi_korisniku(self, user):
        if not self.aktivan:
            return False
        if self.tip == self.Tip.AKCIJA:
            if not self.akcija_artikal_id or not self.akcija_jos_traje():
                return False
        elif not self.slika:
            return False
        if user.is_authenticated:
            return self.za_prijavljene
        return self.za_neprijavljene

    def get_link_href(self):
        if self.tip == self.Tip.AKCIJA and self.akcija_artikal_id:
            return self.akcija_artikal.get_absolute_url()
        if self.link_dugmeta:
            if self.link_dugmeta.startswith(('http://', 'https://', '/')):
                return self.link_dugmeta
            return f'/{self.link_dugmeta.strip("/")}/'
        return reverse('register')

    def __str__(self):
        status = 'aktivan' if self.aktivan else 'neaktivan'
        return f'{self.naziv} ({status})'


SIFRA_MAX_LENGTH = 200
SLUG_MAX_LENGTH = 220
BARKOD_MAX_LENGTH = 200


def _build_unique_slug(model_cls, source_text, *, pk=None, max_length=SLUG_MAX_LENGTH, fallback='item'):
    base_slug = slugify(source_text) or fallback
    base_slug = base_slug[:max_length]
    slug = base_slug
    counter = 1
    while model_cls.objects.filter(slug=slug).exclude(pk=pk).exists():
        suffix = f'-{counter}'
        trim_to = max(1, max_length - len(suffix))
        slug = f'{base_slug[:trim_to]}{suffix}'
        counter += 1
    return slug


class Product(models.Model):
    naziv = models.CharField(max_length=200)
    slug = models.SlugField(max_length=SLUG_MAX_LENGTH, unique=True, blank=True)
    sifra = models.CharField(
        max_length=SIFRA_MAX_LENGTH, blank=True, null=True, unique=True, verbose_name='Šifra',
    )
    barkod = models.CharField(max_length=BARKOD_MAX_LENGTH, blank=True, verbose_name='Barkod')
    opis = models.TextField(
        blank=True,
        verbose_name='Opis',
        help_text='Prikazuje se na stranici artikla.',
    )
    slika = models.ImageField(upload_to='products/', blank=True, null=True)
    na_stanju = models.BooleanField(default=True, verbose_name='Na stanju')
    stanje = models.PositiveIntegerField(default=0, verbose_name='Količina')
    cijena = models.DecimalField(max_digits=10, decimal_places=2)
    akcijska_cijena = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name='Akcijska cijena',
        help_text='Ručni iznos. Ili ostavite prazno i unesite popust (%) ispod.',
    )
    akcija_postotak = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        verbose_name='Popust (%)',
        help_text='Opcionalno — automatski umanjuje redovnu cijenu za ovaj postotak.',
    )
    akcija_do = models.DateField(
        null=True, blank=True,
        verbose_name='Akcija važi do',
        help_text='Opcionalno. Prazno = akcija bez roka. Nakon ovog datuma artikal više nije na akciji.',
    )
    kategorija = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='artikli',
    )
    brend = models.ForeignKey(
        Brand, on_delete=models.SET_NULL, null=True, related_name='artikli',
    )
    tagovi = models.ManyToManyField(
        Tag, blank=True, related_name='artikli', verbose_name='Tagovi',
    )
    prikazi_na_pocetnoj = models.BooleanField(default=True, verbose_name='Prikaži na početnoj')
    proizvedeno_u_japanu = models.BooleanField(
        default=False, verbose_name='Proizvedeno u Japanu',
    )
    aktivan = models.BooleanField(default=True)
    odoo_template_id = models.PositiveIntegerField(
        blank=True, null=True, unique=True, verbose_name='Odoo template ID',
    )
    meta_title = models.CharField(
        max_length=70, blank=True,
        verbose_name='SEO naslov',
        help_text='Opcionalno — ostavi prazno za automatski (naziv artikla).',
    )
    meta_description = models.CharField(
        max_length=160, blank=True,
        verbose_name='Meta opis',
        help_text='Opcionalno — ostavi prazno za automatski opis koji počinje nazivom artikla.',
    )
    olx_listing_id = models.PositiveIntegerField(
        blank=True, null=True, unique=True,
        verbose_name='OLX/Pik ID oglasa',
    )
    olx_listing_slug = models.CharField(
        max_length=220, blank=True,
        verbose_name='OLX/Pik slug',
    )
    olx_listing_url = models.URLField(
        blank=True,
        verbose_name='OLX/Pik link',
    )
    olx_objavljen = models.DateTimeField(
        blank=True, null=True,
        verbose_name='Objavljeno na OLX/Pik',
    )
    kreiran = models.DateTimeField(auto_now_add=True, verbose_name='Dodano')
    azuriran = models.DateTimeField(auto_now=True, verbose_name='Ažurirano')

    class Meta:
        verbose_name = 'Artikal'
        verbose_name_plural = 'Artikli'
        ordering = ['-kreiran']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _build_unique_slug(
                Product,
                self.naziv,
                pk=self.pk,
                fallback='artikal',
            )
        if self.akcija_postotak:
            self.akcijska_cijena = _izracunaj_akcijsku_od_postotka(
                self.cijena, self.akcija_postotak,
            )
        if self.slika:
            from .utils.images import apply_image_processing, process_product_image_manual
            apply_image_processing(self, 'slika', post_process=process_product_image_manual)
        super().save(*args, **kwargs)

    @property
    def na_akciji(self):
        if not _akcija_jos_vazi(self.akcija_do):
            return False
        return self.akcijska_cijena is not None and self.akcijska_cijena < self.cijena

    @property
    def bazna_cijena(self):
        return self.cijena

    @property
    def prikazna_cijena(self):
        if self.na_akciji:
            return self.akcijska_cijena
        return self.cijena

    @property
    def katalog_na_akciji(self):
        if self.na_akciji:
            return True
        return any(variation.na_akciji for variation in self.varijacije.all())

    @property
    def katalog_prikazna_cijena(self):
        variations = list(self.varijacije.all())
        if variations:
            return min(variation.prikazna_cijena for variation in variations)
        return self.prikazna_cijena

    @property
    def katalog_bazna_cijena(self):
        variations = list(self.varijacije.all())
        if variations:
            najjeftinija = min(variations, key=lambda variation: variation.prikazna_cijena)
            return najjeftinija.bazna_cijena
        return self.bazna_cijena

    @property
    def prikaz_akcija_istice(self):
        if not self.katalog_na_akciji or not self.akcija_do:
            return None
        if _akcija_jos_vazi(self.akcija_do):
            return self.akcija_do
        return None

    @property
    def akcija_istice_oznaka(self):
        if not self.katalog_na_akciji:
            return None
        if self.akcija_do and _akcija_jos_vazi(self.akcija_do):
            days = (self.akcija_do - timezone.localdate()).days
            if days == 0:
                return 'Danas ističe'
            if days == 1:
                return 'Još 1 dan'
            return f'Još {days} dana'
        return 'AKCIJA'

    @property
    def katalog_akcija_postotak(self):
        if not self.katalog_na_akciji:
            return None
        return _izracunaj_postotak_umanjenja(
            self.katalog_bazna_cijena,
            self.katalog_prikazna_cijena,
        )

    @property
    def ima_varijacije(self):
        return self.varijacije.exists()

    @property
    def status_dostupnosti(self):
        return 'Na stanju' if self.na_stanju else 'Rasprodato'

    @property
    def prikazna_slika(self):
        if self.slika:
            return self.slika
        for variation in self.varijacije.all():
            if variation.slika:
                return variation.slika
        return None

    @property
    def ima_sliku(self):
        return bool(self.prikazna_slika)

    @property
    def prikazna_slika_responsive(self):
        from .utils.images import product_image_responsive_meta

        slika = self.prikazna_slika
        if not slika:
            return None
        return product_image_responsive_meta(slika)

    @property
    def seo_title(self):
        """Koristi se za <title> i og:title kad meta_title nije unesen."""
        return self.meta_title or self.naziv

    @property
    def seo_description(self):
        """Koristi se za meta description kad meta_description nije unesen."""
        if self.meta_description:
            return self.meta_description
        return (
            f"{self.naziv}. Kupite kvalitetnu ribolovačku opremu online u Bosni i Hercegovini. "
            "Štapovi, mašinice, varalice, najloni, hranilice, pribor i oprema poznatih svjetskih brendova po odličnim cijenama."
        )

    def get_absolute_url(self):
        return reverse('product_detail', kwargs={'slug': self.slug})

    def __str__(self):
        return self.naziv


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='dodatne_slike',
        verbose_name='Artikal',
    )
    slika = models.ImageField(
        upload_to='products/',
        verbose_name='Dodatna slika',
    )
    redoslijed = models.PositiveIntegerField(
        default=0,
        verbose_name='Redoslijed (manji broj = prije)',
    )

    class Meta:
        verbose_name = 'Dodatna slika artikla'
        verbose_name_plural = 'Dodatne slike artikla'
        ordering = ['redoslijed', 'id']

    def __str__(self):
        return f"Dodatna slika za {self.product.naziv}"

    def save(self, *args, **kwargs):
        if self.slika:
            from .utils.images import apply_image_processing, process_product_image_manual
            apply_image_processing(self, 'slika', post_process=process_product_image_manual)
        super().save(*args, **kwargs)

    @property
    def prikazna_slika(self):
        return self.slika

    @property
    def prikazna_slika_responsive(self):
        from .utils.images import product_image_responsive_meta
        if not self.slika:
            return None
        return product_image_responsive_meta(self.slika)


class ProductVariation(models.Model):
    artikal = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='varijacije',
    )
    naziv = models.CharField(max_length=100)
    sifra = models.CharField(
        max_length=SIFRA_MAX_LENGTH, blank=True, null=True, unique=True, verbose_name='Šifra',
    )
    slika = models.ImageField(upload_to='products/variations/', blank=True, null=True)
    cijena = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Ostavite prazno za cijenu artikla',
    )
    akcijska_cijena = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name='Akcijska cijena',
        help_text='Ručni iznos za varijaciju. Ili unesite popust (%) ispod.',
    )
    akcija_postotak = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        verbose_name='Popust (%)',
        help_text='Opcionalno — umanjuje cijenu ove varijacije za ovaj postotak.',
    )
    na_stanju = models.BooleanField(default=True, verbose_name='Na stanju')
    stanje = models.PositiveIntegerField(default=0, verbose_name='Količina')
    redoslijed = models.PositiveIntegerField(default=0)
    odoo_template_id = models.PositiveIntegerField(
        blank=True, null=True, unique=True, verbose_name='Odoo template ID',
    )
    odoo_variant_id = models.PositiveIntegerField(
        blank=True, null=True, unique=True, verbose_name='Odoo variant ID',
    )

    class Meta:
        verbose_name = 'Varijacija'
        verbose_name_plural = 'Varijacije'
        ordering = ['redoslijed', 'id']

    @property
    def bazna_cijena(self):
        return self.cijena if self.cijena is not None else self.artikal.cijena

    @property
    def efektivna_akcijska_cijena(self):
        if self.akcijska_cijena is not None and self.akcijska_cijena < self.bazna_cijena:
            return self.akcijska_cijena
        if self.artikal.na_akciji:
            ratio = self.artikal.akcijska_cijena / self.artikal.cijena
            return (self.bazna_cijena * ratio).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return None

    @property
    def na_akciji(self):
        return self.efektivna_akcijska_cijena is not None

    @property
    def prikazna_cijena(self):
        if self.na_akciji:
            return self.efektivna_akcijska_cijena
        return self.bazna_cijena

    @property
    def status_dostupnosti(self):
        return 'Na stanju' if self.na_stanju else 'Rasprodato'

    @property
    def ima_sliku(self):
        return bool(self.slika)

    def save(self, *args, **kwargs):
        if self.akcija_postotak:
            self.akcijska_cijena = _izracunaj_akcijsku_od_postotka(
                self.bazna_cijena, self.akcija_postotak,
            )
        if self.slika:
            from .utils.images import apply_image_processing, process_product_image_manual
            apply_image_processing(self, 'slika', post_process=process_product_image_manual)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.artikal.naziv} — {self.naziv}'


class UpsellOffer(models.Model):
    class PrikazTip(models.TextChoices):
        POPUP = 'popup', 'Popup'
        BANNER_IZNAD = 'banner_iznad', 'Baner iznad artikala u korpi'
        BANNER_ISPOD = 'banner_ispod', 'Baner ispod "Nastavi na narudžbu"'
        CHECKOUT = 'checkout', 'Checkout — poslednja šansa'

    naziv = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='Interni naziv',
        help_text='Opcionalno — samo za prepoznavanje u adminu.',
    )
    ponuda_artikli = models.ManyToManyField(
        Product,
        blank=True,
        verbose_name='Artikli za prikaz',
        help_text='Opcionalno — artikli koji se nude u popupu ili na baneru u korpi.',
    )
    popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust (%)',
        help_text='Opcionalno - popust na cijenu ponuđenih artikala.',
    )
    popust_km = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust (KM)',
        help_text='Opcionalno - fiksni popust u KM na ponuđene artikle.',
    )
    trigger_artikal = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='upsell_triggeri',
        verbose_name='Trigger artikal',
        help_text='Ako se ovaj artikal doda u korpu, pokreni ponudu.',
    )
    trigger_kategorija = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='upsell_triggeri',
        verbose_name='Trigger kategorija',
        help_text='Ako se artikal iz ove kategorije doda u korpu, pokreni ponudu.',
    )

    # === X+1 Quantity Deal (1+1 / 2+1 / 3+1) ===
    deal_artikal = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quantity_deals',
        verbose_name='Artikal za X+1 deal',
        help_text='Artikal na koji se odnosi 1+1 / 2+1 / 3+1 ponuda.',
    )
    deal_vrsta = models.CharField(
        max_length=10,
        choices=[
            ('1+1', '1+1 (kupi 1, drugi snižen)'),
            ('2+1', '2+1 (kupi 2, treći snižen)'),
            ('3+1', '3+1 (kupi 3, četvrti snižen)'),
        ],
        blank=True,
        null=True,
        verbose_name='Vrsta prodaje',
    )
    deal_popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust % na +1 artikal',
        help_text='npr. 50 = 50% popusta na 3. artikal. 100 = GRATIS.',
    )

    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    naslov_ponude = models.CharField(
        max_length=100,
        default='Specijalna ponuda za vas!',
        verbose_name='Naslov u popupu',
        help_text='Možeš izmijeniti tekst "Specijalna ponuda za vas!"',
    )
    opis_ponude = models.CharField(
        max_length=200,
        default='Dodajte u korpu sa dodatnim popustom',
        verbose_name='Opis u popupu',
        help_text='Možeš izmijeniti tekst "Dodajte u korpu sa dodatnim popustom"',
    )
    prikaz = models.CharField(
        max_length=20,
        choices=PrikazTip.choices,
        default=PrikazTip.BANNER_IZNAD,
        verbose_name='Gdje prikazati',
    )
    baner_slika = models.ImageField(
        upload_to='upsell/',
        blank=True,
        null=True,
        verbose_name='Baner slika',
        help_text='Opcionalno — preporučeno široko i nisko (npr. 1200×200 px).',
    )
    tekst_dugmeta = models.CharField(
        max_length=50,
        default='Dodaj u korpu',
        verbose_name='Tekst dugmeta',
    )

    class Meta:
        verbose_name = 'Upsell ponuda'
        verbose_name_plural = 'Upsell ponude'
        ordering = ['redoslijed', '-id']

    def get_trigger_display(self):
        if self.trigger_artikal:
            return f'Artikal: {self.trigger_artikal.naziv}'
        if self.trigger_kategorija:
            return f'Kategorija: {self.trigger_kategorija.naziv}'
        return 'Nema trigger'

    def __str__(self):
        if self.naziv:
            return self.naziv
        return f'Upsell #{self.pk}' if self.pk else 'Upsell ponuda'


class LoyaltyCard(models.Model):
    class Nivo(models.TextChoices):
        BRONZA = 'bronza', 'Bronza'
        SREBRNA = 'srebrna', 'Srebrna'
        ZLATNA = 'zlatna', 'Zlatna'
        PLATINUM = 'platinum', 'Platinum'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='loyalty_kartica',
    )
    kod = models.CharField(max_length=20, unique=True, verbose_name='Online kod')
    barkod = models.CharField(max_length=20, unique=True, verbose_name='Barkod')
    nivo = models.CharField(max_length=20, choices=Nivo.choices, default=Nivo.BRONZA)
    ukupna_potrosnja = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    kreirana = models.DateTimeField(auto_now_add=True)
    azurirana = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Loyalty kartica'
        verbose_name_plural = 'Loyalty kartice'

    @property
    def postotak(self):
        from .loyalty import tier_info
        return tier_info(self.nivo)['postotak']

    def __str__(self):
        return f'{self.user} — {self.get_nivo_display()} ({self.kod})'


class Coupon(models.Model):
    kod = models.CharField(max_length=20, unique=True)
    naziv = models.CharField(max_length=100)
    postotak = models.DecimalField(max_digits=5, decimal_places=2)
    vlasnik = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kuponi',
        null=True,
        blank=True,
        verbose_name='Vlasnik (samo on može koristiti)',
    )
    loyalty_kartica = models.OneToOneField(
        LoyaltyCard,
        on_delete=models.CASCADE,
        related_name='kupon',
        null=True,
        blank=True,
    )
    aktivan = models.BooleanField(default=True)
    automatski = models.BooleanField(
        default=False,
        verbose_name='Automatski (loyalty)',
        help_text='Kreiran i ažuriran iz loyalty kartice.',
    )
    kreiran = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Kupon'
        verbose_name_plural = 'Kuponi'

    def __str__(self):
        return f'{self.kod} — {self.postotak}%'


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profil',
    )
    telefon = models.CharField(max_length=30, blank=True)
    adresa = models.CharField(max_length=300, blank=True)
    grad = models.CharField(max_length=100, blank=True)
    postanski_broj = models.CharField(max_length=20, blank=True)

    class Meta:
        verbose_name = 'Korisnički profil'
        verbose_name_plural = 'Korisnički profili'

    @property
    def puno_ime(self):
        return self.user.get_full_name() or self.user.email

    def __str__(self):
        return self.puno_ime


class Order(models.Model):
    class Status(models.TextChoices):
        NOVA = 'nova', 'Nova'
        POTVRDJENA = 'potvrdjena', 'Potvrđena'
        POSLANA = 'poslana', 'Poslana'
        ZAVRSENA = 'zavrsena', 'Završena'
        OTKAZANA = 'otkazana', 'Otkazana'

    korisnik = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='narudzbe',
        verbose_name='Korisnik',
    )
    broj = models.CharField(max_length=20, unique=True, editable=False)
    ime_prezime = models.CharField(max_length=200)
    email = models.EmailField()
    telefon = models.CharField(max_length=30)
    adresa = models.CharField(max_length=300)
    grad = models.CharField(max_length=100)
    postanski_broj = models.CharField(max_length=20, blank=True)
    napomena = models.TextField(blank=True)
    medjuzbir = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    dostava = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    popust = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    kupon_kod = models.CharField(max_length=20, blank=True)
    ukupno = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOVA)
    kreirana = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Narudžba'
        verbose_name_plural = 'Narudžbe'
        ordering = ['-kreirana']

    def save(self, *args, **kwargs):
        if not self.broj:
            from django.utils import timezone
            prefix = timezone.localtime().strftime('%Y%m%d')
            last = Order.objects.filter(broj__startswith=prefix).order_by('-broj').first()
            seq = int(last.broj[-4:]) + 1 if last else 1
            self.broj = f'{prefix}{seq:04d}'
        super().save(*args, **kwargs)

    @property
    def pdv_pregled(self):
        from .cart import izracunaj_pdv
        return izracunaj_pdv(self.ukupno)

    @property
    def dostava_naziv(self):
        return SiteSettings.load().dostava_naziv

    def get_status_label(self):
        return self.Status(self.status).label

    def __str__(self):
        return f'#{self.broj} — {self.ime_prezime}'


class OrderItem(models.Model):
    narudzba = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='stavke')
    artikal = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    varijacija = models.ForeignKey(ProductVariation, on_delete=models.SET_NULL, null=True, blank=True)
    naziv = models.CharField(max_length=200)
    product_naziv = models.CharField(max_length=200, blank=True)
    varijacija_naziv = models.CharField(max_length=100, blank=True)
    sifra = models.CharField(max_length=SIFRA_MAX_LENGTH, blank=True)
    cijena = models.DecimalField(max_digits=10, decimal_places=2)
    kolicina = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name = 'Stavka narudžbe'
        verbose_name_plural = 'Stavke narudžbe'

    @property
    def ukupno(self):
        return self.cijena * self.kolicina

    @property
    def kolicina_range(self):
        return range(self.kolicina)

    @property
    def puni_naziv(self):
        if self.varijacija_naziv:
            return f'{self.product_naziv or self.naziv} — {self.varijacija_naziv}'
        return self.product_naziv or self.naziv

    def __str__(self):
        return f'{self.puni_naziv} × {self.kolicina}'


class ChatConversation(models.Model):
    class Status(models.TextChoices):
        OPEN = 'open', 'Otvoren'
        CLOSED = 'closed', 'Zatvoren'

    session_key = models.CharField(max_length=40, db_index=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chat_conversations',
    )
    guest_name = models.CharField(max_length=120, blank=True)
    guest_email = models.EmailField(blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.OPEN,
    )
    staff_unread_count = models.PositiveIntegerField(default=0)
    customer_unread_count = models.PositiveIntegerField(default=0)
    last_message_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Chat razgovor'
        verbose_name_plural = 'Chat razgovori'
        ordering = ['-last_message_at']

    @property
    def display_name(self):
        if self.user_id:
            full_name = self.user.get_full_name().strip()
            return full_name or self.user.email
        return self.guest_name.strip() or 'Gost'

    @property
    def display_email(self):
        if self.user_id:
            return self.user.email
        return self.guest_email

    @property
    def is_registered(self):
        return bool(self.user_id)

    def __str__(self):
        return f'Chat — {self.display_name}'


class ChatMessage(models.Model):
    class Sender(models.TextChoices):
        CUSTOMER = 'customer', 'Kupac'
        STAFF = 'staff', 'Podrška'

    conversation = models.ForeignKey(
        ChatConversation,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender_type = models.CharField(max_length=10, choices=Sender.choices)
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chat_replies',
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    read_by_staff = models.BooleanField(default=False)
    read_by_customer = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Chat poruka'
        verbose_name_plural = 'Chat poruke'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.get_sender_type_display()}: {self.body[:40]}'


class MarketingEmailCampaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Nacrt'
        SENDING = 'sending', 'Slanje u toku'
        SENT = 'sent', 'Poslano'
        FAILED = 'failed', 'Greška'

    naslov = models.CharField(max_length=200, verbose_name='Naslov emaila')
    uvod = models.TextField(
        blank=True,
        verbose_name='Uvodni tekst',
        help_text='Kratka poruka ispod bannera (opcionalno).',
    )
    banner = models.ImageField(
        upload_to='marketing/',
        verbose_name='Banner slika',
    )
    cta_link = models.URLField(
        blank=True,
        verbose_name='Link dugmeta',
        help_text='Gdje vodi klik na banner / dugme. Prazno = akcijska ponuda na početnoj.',
    )
    cta_tekst = models.CharField(
        max_length=120,
        default='Pogledaj akcijsku ponudu',
        verbose_name='Tekst dugmeta',
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    broj_primaoca = models.PositiveIntegerField(default=0)
    broj_gresaka = models.PositiveIntegerField(default=0)
    slanje_offset = models.PositiveIntegerField(default=0)
    slanje_ukupno = models.PositiveIntegerField(default=0)
    slanje_lista = models.JSONField(default=list, blank=True)
    slanje_poslati = models.JSONField(
        default=list,
        blank=True,
        help_text='Email adrese kojima je kampanja uspješno poslana (bez duplikata pri nastavku).',
    )
    slanje_grupa = models.ForeignKey(
        'MarketingSubscriberGroup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kampanje',
        verbose_name='Posljednja odabrana grupa',
    )
    slanje_ukljuci_registrovane = models.BooleanField(default=False)
    poslano = models.DateTimeField(null=True, blank=True)
    kreirano = models.DateTimeField(auto_now_add=True)
    poslao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketing_kampanje',
        verbose_name='Poslao',
    )

    class Meta:
        verbose_name = 'Marketing email kampanja'
        verbose_name_plural = 'Marketing email kampanje'
        ordering = ['-kreirano']

    def __str__(self):
        return self.naslov

    @property
    def effective_cta_link(self):
        if self.cta_link:
            return self.cta_link
        from django.conf import settings as django_settings
        return f'{django_settings.SITE_URL.rstrip("/")}/?akcija=1#product-showcase'


class MarketingSubscriberGroup(models.Model):
    naziv = models.CharField(max_length=80, verbose_name='Naziv grupe')
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    kreirano = models.DateTimeField(auto_now_add=True)
    dodao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketing_grupe',
        verbose_name='Kreirao',
    )

    class Meta:
        verbose_name = 'Marketing grupa'
        verbose_name_plural = 'Marketing grupe'
        ordering = ['redoslijed', 'id']

    def __str__(self):
        return self.naziv

    @property
    def active_count(self):
        return self.pretplatnici.filter(aktivan=True).count()


class MarketingSubscriber(models.Model):
    class Source(models.TextChoices):
        MANUAL = 'manual', 'Ručno'
        ORDER = 'order', 'Narudžba'
        IMPORT = 'import', 'Import'

    email = models.EmailField(unique=True, verbose_name='Email')
    ime = models.CharField(max_length=120, blank=True, verbose_name='Ime')
    grupa = models.ForeignKey(
        MarketingSubscriberGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pretplatnici',
        verbose_name='Grupa',
    )
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')
    izvor = models.CharField(
        max_length=10,
        choices=Source.choices,
        default=Source.MANUAL,
        verbose_name='Izvor',
    )
    dodao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketing_pretplatnici',
        verbose_name='Dodao',
    )
    kreirano = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Marketing pretplatnik'
        verbose_name_plural = 'Marketing pretplatnici'
        ordering = ['-kreirano']
        indexes = [
            models.Index(fields=['aktivan']),
        ]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class ActiveCartItem(models.Model):
    """Trenutne stavke u korpama posjetilaca (usklađeno sa sesijom)."""
    session_key = models.CharField(max_length=40, db_index=True, verbose_name='Sesija')
    line_key = models.CharField(max_length=64, verbose_name='Stavka')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='aktivne_korpe_stavke',
        verbose_name='Korisnik',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='aktivne_korpe_stavke',
        verbose_name='Artikal',
    )
    variation = models.ForeignKey(
        ProductVariation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='aktivne_korpe_stavke',
        verbose_name='Varijacija',
    )
    naziv = models.CharField(max_length=200, verbose_name='Naziv')
    varijacija_naziv = models.CharField(max_length=100, blank=True, verbose_name='Varijacija naziv')
    kolicina = models.PositiveIntegerField(default=1, verbose_name='Količina')
    cijena = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Cijena')
    ukupno = models.DecimalField(max_digits=10, decimal_places=2, verbose_name='Ukupno')
    dodano = models.DateTimeField(auto_now_add=True, verbose_name='Dodano u korpu')
    azurirano = models.DateTimeField(auto_now=True, verbose_name='Zadnja izmjena')

    class Meta:
        verbose_name = 'Stavka aktivne korpe'
        verbose_name_plural = 'Stavke aktivnih korpi'
        ordering = ['-azurirano']
        constraints = [
            models.UniqueConstraint(
                fields=['session_key', 'line_key'],
                name='uniq_active_cart_session_line',
            ),
        ]
        indexes = [
            models.Index(fields=['-azurirano']),
            models.Index(fields=['user', '-azurirano']),
        ]

    def __str__(self):
        return f'{self.naziv} × {self.kolicina}'


class SiteVisitorIdentity(models.Model):
    """
    Trajni token posjetioca (cookie) — broj dolazaka na sajt preko sesija.
    """
    token = models.CharField(max_length=64, unique=True, db_index=True, verbose_name='Token')
    visit_count = models.PositiveIntegerField(default=1, verbose_name='Broj posjeta')
    last_session_key = models.CharField(max_length=40, blank=True, db_index=True)
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name='Prva posjeta')
    last_seen = models.DateTimeField(auto_now=True, verbose_name='Zadnja posjeta')

    class Meta:
        verbose_name = 'Identitet posjetioca'
        verbose_name_plural = 'Identiteti posjetilaca'
        ordering = ['-last_seen']

    def __str__(self):
        return f'{self.token[:8]}… ({self.visit_count}×)'

    @property
    def is_returning(self):
        return self.visit_count > 1


class LiveVisitor(models.Model):
    """Posjetilac sajta — zadnja aktivnost po sesiji."""
    session_key = models.CharField(max_length=40, unique=True, db_index=True, verbose_name='Sesija')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='live_visits',
        verbose_name='Korisnik',
    )
    ime = models.CharField(max_length=120, blank=True, verbose_name='Ime')
    email = models.EmailField(blank=True, verbose_name='Email')
    grad = models.CharField(max_length=100, blank=True, verbose_name='Grad')
    drzava = models.CharField(max_length=2, blank=True, verbose_name='Država')
    ip_adresa = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP adresa')
    visitor_token = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name='Trajni token',
        help_text='Cookie ozb_vid — veza preko sesija.',
    )
    site_visit_count = models.PositiveIntegerField(
        default=1, verbose_name='Broj dolazaka na sajt',
        help_text='>1 = vraćeni posjetilac (nije prvi put).',
    )
    pregledane_kategorije = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Pregledane kategorije',
        help_text='Nazivi kategorija koje je posjetilac pregledao u ovoj sesiji (najnovije prvo).',
    )
    pregledani_proizvodi = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Pregledani proizvodi',
        help_text='Lista {id, naziv, views} — proizvodi koje je posjetilac otvorio u ovoj sesiji.',
    )
    skoro_korpa = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Skoro dodao u korpu',
        help_text=(
            'Artikli gdje je kursor bio na „Dodaj u korpu” ali nije kliknuo. '
            'Lista {id, naziv, hovers, last_at} — najjači intent za #1 ponudu.'
        ),
    )
    izvor_dolaska = models.CharField(
        max_length=20,
        blank=True,
        verbose_name='Izvor dolaska',
        help_text='facebook / google / instagram / direct / other',
    )
    trenutna_putanja = models.CharField(
        max_length=300,
        blank=True,
        verbose_name='Trenutna putanja',
        help_text='URL putanja na kojoj je kupac sada (za live analitiku).',
    )
    trenutno_gleda = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Trenutno gleda',
        help_text='Čitljiv opis: artikal, kategorija, korpa, početna…',
    )
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name='Prva aktivnost')
    last_seen = models.DateTimeField(db_index=True, verbose_name='Zadnja aktivnost')

    class Meta:
        verbose_name = 'Posjetilac (uzivo)'
        verbose_name_plural = 'Posjetioci (uzivo)'
        ordering = ['-last_seen']
        indexes = [
            models.Index(fields=['-last_seen']),
            models.Index(fields=['email', '-last_seen']),
            models.Index(fields=['user', '-last_seen']),
        ]

    def __str__(self):
        label = self.email or self.ime or self.session_key[:8]
        return label


class CityVisitTotal(models.Model):
    """Kumulativni broj posjeta po gradu — samo raste, ne resetuje se s filterom datuma."""
    grad = models.CharField(max_length=100, unique=True, verbose_name='Grad')
    broj_posjeta = models.PositiveIntegerField(default=0, verbose_name='Broj posjeta')
    azurirano = models.DateTimeField(auto_now=True, verbose_name='Zadnje ažuriranje')

    class Meta:
        verbose_name = 'Posjete po gradu (ukupno)'
        verbose_name_plural = 'Posjete po gradovima (ukupno)'
        ordering = ['-broj_posjeta', 'grad']

    def __str__(self):
        return f'{self.grad} — {self.broj_posjeta}'


class StaffSiteEvent(models.Model):
    """Live obavijest za superusere (online, korpa, registracija, kupovina)."""

    class Tip(models.TextChoices):
        ONLINE = 'online', 'Online na sajtu'
        CART = 'cart', 'Dodano u korpu'
        REGISTER = 'register', 'Registracija'
        PURCHASE = 'purchase', 'Kupovina'

    tip = models.CharField(max_length=20, choices=Tip.choices, db_index=True)
    naslov = models.CharField(max_length=120)
    poruka = models.CharField(max_length=300)
    ime = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    grad = models.CharField(max_length=100, blank=True)
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    kreirano = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Staff live obavijest'
        verbose_name_plural = 'Staff live obavijesti'
        ordering = ['-kreirano']
        indexes = [
            models.Index(fields=['-kreirano', 'id']),
        ]

    def __str__(self):
        return f'{self.get_tip_display()}: {self.naslov}'


class LiveVisitorOffer(models.Model):
    """Staff ponuda posjetiocu koji je trenutno na sajtu (artikal, popust ili registracija)."""

    class Tip(models.TextChoices):
        ARTIKAL = 'artikal', 'Artikal'
        NARUDZBA = 'narudzba', 'Popust na narudžbu' 
        REGISTRACIJA = 'registracija', 'Registracija'

    session_key = models.CharField(max_length=40, db_index=True, verbose_name='Sesija')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='live_visitor_offers_received',
        verbose_name='Posjetilac',
    )
    tip = models.CharField(
        max_length=20,
        choices=Tip.choices,
        default=Tip.ARTIKAL,
        verbose_name='Tip ponude',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='live_visitor_offers',
        null=True,
        blank=True,
        verbose_name='Artikal',
    )
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, verbose_name='Popust (%)',
    )
    besplatna_dostava = models.BooleanField(
        default=False,
        verbose_name='Besplatna dostava (prva kupovina)',
        help_text='Ako je uključeno, kupac na prvu narudžbu ostvaruje besplatnu dostavu.',
    )
    aktivacioni_kod = models.CharField(
        max_length=20, blank=True, verbose_name='Aktivacioni kod',
    )
    show_popup = models.BooleanField(default=True, verbose_name='Prikaži popup')
    added_to_cart = models.BooleanField(default=False, verbose_name='Dodano u korpu')
    kod_aktiviran = models.BooleanField(default=False, verbose_name='Kod aktiviran')
    poslao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='live_visitor_offers_sent',
        verbose_name='Poslao',
    )
    kreirano = models.DateTimeField(auto_now_add=True)
    azurirano = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Uzivo ponuda posjetiocu'
        verbose_name_plural = 'Uzivo ponude posjetiocima'
        ordering = ['-azurirano']

    def __str__(self):
        return f'{self.product_id} → {self.session_key[:8]}…'


class CartRecoveryAlert(models.Model):
    """Admin podsjetnik kupcu da završi kupovinu (opcionalno s popustom)."""
    session_key = models.CharField(max_length=40, db_index=True, verbose_name='Sesija')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='cart_recovery_alerts_received',
        verbose_name='Kupac',
    )
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, verbose_name='Popust (%)',
    )
    show_popup = models.BooleanField(default=True, verbose_name='Prikaži popup')
    discount_applied = models.BooleanField(default=False, verbose_name='Popust iskorišten')
    poslao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cart_recovery_alerts',
        verbose_name='Poslao',
    )
    kreirano = models.DateTimeField(auto_now_add=True)
    azurirano = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Podsjetnik korpe'
        verbose_name_plural = 'Podsjetnici korpi'
        ordering = ['-azurirano']

    def __str__(self):
        return f'{self.session_key[:8]}… ({self.discount_percent}%)'


class OnlineGiftCampaign(models.Model):
    """
    Online nagrada za posjetioce koji su trenutno na sajtu.
    Jednostavan otkrij-nagradu popup (bez točka / greb-greba).
    """

    class Audience(models.TextChoices):
        ALL = 'all', 'Svi online posjetioci'
        REGISTERED = 'registered', 'Samo registrovani online'

    class PrizeType(models.TextChoices):
        PRODUCT = 'product', 'Gratis artikal (100%)'
        PERCENT = 'percent', '% na kompletnu narudžbu'
        FIXED_KM = 'fixed_km', 'KM iznos popusta'
        FREE_SHIPPING = 'free_shipping', 'Besplatna dostava'

    naziv = models.CharField(
        max_length=100,
        verbose_name='Interni naziv',
        help_text='Samo za admin (npr. „Vikend online poklon”).',
    )
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')
    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.ALL,
        verbose_name='Kome prikazati',
    )
    prize_type = models.CharField(
        max_length=20,
        choices=PrizeType.choices,
        default=PrizeType.PERCENT,
        verbose_name='Tip nagrade',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='online_gift_campaigns',
        verbose_name='Artikal (gratis)',
        help_text='Za tip gratis artikal.',
    )
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        verbose_name='Popust %',
        help_text='Za tip % na narudžbu (jednokratno).',
    )
    discount_km = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name='Popust KM',
        help_text='Za tip fiksni KM popust (jednokratno).',
    )
    win_chance_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('30.00'),
        verbose_name='Šansa za nagradu (%)',
        help_text='Koliko % online posjetilaca dobije nagradu (ostali vide „sreću drugi put”).',
    )
    naslov = models.CharField(
        max_length=120,
        default='Online nagrada za tebe!',
        verbose_name='Naslov',
    )
    poruka = models.CharField(
        max_length=220,
        blank=True,
        default='Kao hvala što ste na sajtu — otkrijte da li ste dobili poklon.',
        verbose_name='Poruka',
    )
    popup_delay_seconds = models.PositiveSmallIntegerField(
        default=3,
        verbose_name='Prikaži nakon (sekundi)',
        help_text='0 = odmah.',
    )
    only_tracked_online = models.BooleanField(
        default=False,
        verbose_name='Samo praćeni online posjetioci',
        help_text='Ako je uključeno, nagrada se nudi samo onima koje vidiš u Uživo analitici (LiveVisitor).',
    )
    automatic = models.BooleanField(
        default=True,
        verbose_name='Automatski online (nakon 4 min)',
        help_text=(
            'Uključeno: nakon ~4 min na sajtu iskače nagradna igra '
            '(prije toga na 2 min ide personalizovana ponuda prema gledanju). '
            'Igranje je samo za registrovane (gost vidi „Registruj se i igraj”). '
            'Isključeno: nagrada se ne pojavljuje sama — staff je pušta ručno u Uživo analitici.'
        ),
    )
    once_per_visitor = models.BooleanField(
        default=True,
        verbose_name='Jednom po posjetiocu',
    )
    kreirano = models.DateTimeField(auto_now_add=True)
    azurirano = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Online nagrada'
        verbose_name_plural = 'Online nagrade'
        ordering = ['-aktivan', '-azurirano']

    def __str__(self):
        status = 'aktivna' if self.aktivan else 'neaktivna'
        return f'{self.naziv} ({status})'

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.win_chance_percent is not None and (
            self.win_chance_percent < 0 or self.win_chance_percent > 100
        ):
            raise ValidationError({'win_chance_percent': 'Šansa mora biti 0–100%.'})
        if self.prize_type == self.PrizeType.PRODUCT and not self.product_id:
            raise ValidationError({'product': 'Odaberite artikal.'})
        if self.prize_type == self.PrizeType.PERCENT:
            if not self.discount_percent or self.discount_percent <= 0:
                raise ValidationError({'discount_percent': 'Unesite % veći od 0.'})
        if self.prize_type == self.PrizeType.FIXED_KM:
            if not self.discount_km or self.discount_km <= 0:
                raise ValidationError({'discount_km': 'Unesite KM veći od 0.'})

    def prize_label(self):
        if self.prize_type == self.PrizeType.PRODUCT:
            name = ''
            if self.product_id and self.product:
                name = (self.product.naziv or '')[:40]
            return f'GRATIS {name}'.strip() or 'Gratis artikal'
        if self.prize_type == self.PrizeType.PERCENT:
            pct = self.discount_percent or 0
            pct_label = int(pct) if pct == int(pct) else pct
            return f'{pct_label}% na narudžbu'
        if self.prize_type == self.PrizeType.FIXED_KM:
            km = self.discount_km or 0
            km_label = int(km) if km == int(km) else km
            return f'-{km_label} KM'
        if self.prize_type == self.PrizeType.FREE_SHIPPING:
            return 'Besplatna dostava'
        return 'Nagrada'

    def audience_matches(self, user):
        if self.audience == self.Audience.REGISTERED:
            return bool(user and getattr(user, 'is_authenticated', False))
        return True


class OnlineGiftClaim(models.Model):
    """Jedan pokušaj / nagrada online posjetioca."""

    campaign = models.ForeignKey(
        OnlineGiftCampaign,
        on_delete=models.CASCADE,
        related_name='claims',
        verbose_name='Kampanja',
    )
    session_key = models.CharField(max_length=40, db_index=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='online_gift_claims',
    )
    won = models.BooleanField(default=False)
    prize_type = models.CharField(max_length=20, blank=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='online_gift_claims',
    )
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_km = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    reward_claimed = models.BooleanField(default=False)
    reward_consumed = models.BooleanField(default=False)
    order = models.ForeignKey(
        'Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='online_gift_claims',
        verbose_name='Narudžba',
        help_text='Popunjava se kad kupac iskoristi nagradu u checkoutu.',
    )
    kreirano = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Online nagrada (pokušaj)'
        verbose_name_plural = 'Online nagrade (pokušaji)'
        ordering = ['-kreirano']
        indexes = [
            models.Index(fields=['session_key', 'campaign']),
            models.Index(fields=['user', 'campaign']),
            models.Index(fields=['won', '-kreirano']),
        ]

    def __str__(self):
        return f'Claim #{self.pk} ({"pobjeda" if self.won else "promašaj"})'

    def prize_label(self):
        """Ljudski čitljiv naziv osvojene nagrade."""
        if self.prize_type == OnlineGiftCampaign.PrizeType.PRODUCT:
            name = ''
            if self.product_id and self.product:
                name = (self.product.naziv or '')[:40]
            return f'GRATIS {name}'.strip() or 'Gratis artikal'
        if self.prize_type == OnlineGiftCampaign.PrizeType.PERCENT:
            pct = self.discount_percent or 0
            pct_label = int(pct) if pct == int(pct) else pct
            return f'{pct_label}% na narudžbu'
        if self.prize_type == OnlineGiftCampaign.PrizeType.FIXED_KM:
            km = self.discount_km or 0
            km_label = int(km) if km == int(km) else km
            return f'-{km_label} KM'
        if self.prize_type == OnlineGiftCampaign.PrizeType.FREE_SHIPPING:
            return 'Besplatna dostava'
        if self.campaign_id:
            try:
                return self.campaign.prize_label()
            except Exception:
                pass
        return 'Nagrada'


class OnlineGiftPush(models.Model):
    """
    Staff ručno pušta online nagradu određenom posjetiocu (sesija).
    Koristi se kad kampanja nije u automatskom režimu.
    """

    campaign = models.ForeignKey(
        OnlineGiftCampaign,
        on_delete=models.CASCADE,
        related_name='pushes',
        verbose_name='Kampanja',
    )
    session_key = models.CharField(max_length=40, db_index=True, verbose_name='Sesija')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='online_gift_pushes',
        verbose_name='Kupac',
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='online_gift_pushes_sent',
        verbose_name='Staff',
    )
    played = models.BooleanField(default=False, verbose_name='Otvorio nagradu')
    dismissed = models.BooleanField(default=False, verbose_name='Zatvorio')
    kreirano = models.DateTimeField(auto_now_add=True)
    azurirano = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Online nagrada (ručno)'
        verbose_name_plural = 'Online nagrade (ručno)'
        ordering = ['-kreirano']
        indexes = [
            models.Index(fields=['session_key', 'campaign', 'played']),
        ]

    def __str__(self):
        return f'Push #{self.pk} → {self.session_key[:8]}…'