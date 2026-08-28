import re
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
        help_text='Prikazuje se u headeru (crna pozadina). Originalna slika se ne ofarbava — samo max ~640×128. Preporuka: PNG, transparentna pozadina, bijela/zelena grafika.',
    )
    logo_glavni_sajt = models.ImageField(
        upload_to='site/', blank=True, null=True,
        verbose_name='Logo glavnog sajta',
        help_text=(
            'Prikazuje se u headeru ispod glavnog loga uz tekst „by” '
            '(pod-sajt / Carpologija BH). Automatski se skalira na ~200×48px PNG.'
        ),
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
        help_text=(
            'Opcionalno (0–50). Prazno ili 0 = ponuda bez popusta '
            '(nema −% / „akcija” teksta). Unesi % samo ako želiš sniženje.'
        ),
    )
    browse_interest_popup_aktivan = models.BooleanField(
        default=True,
        verbose_name='AI prodaja aktivna',
        help_text=(
            'AI prati kupca (šta gleda, koliko dugo, skoro-korpa) i u pravom trenutku '
            'šalje popup ponudu na 1–2 artikla. Max 2 ponude po posjeti, s razmakom. '
            'Popust % podesi ispod — 0 = ponuda bez popusta.'
        ),
    )
    browse_interest_popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        null=True,
        blank=True,
        verbose_name='AI prodaja — popust (%)',
        help_text=(
            'Popust na automatsku ponudu (0–50). '
            '0 = samo preporuka / ponuda, bez sniženja cijene. '
            'Npr. 5, 10, 15…'
        ),
    )
    product_dwell_popup_aktivan = models.BooleanField(
        default=False,
        verbose_name='AI dwell (odmah na artiklu) aktivan',
        help_text=(
            'Čim kupac uđe na artikal: NEMA popup-a. '
            'Odmah se precrta stara cijena, pojavi se snizena i odbrojavanje 2 min. '
            'Kad istekne — u toj posjeti više nema ponude. '
            'Artikle i % popusta unosiš u tabeli „AI dwell artikli” (po artiklu).'
        ),
    )
    product_dwell_popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        null=True,
        blank=True,
        verbose_name='AI dwell — default popust (%)',
        help_text=(
            'Fallback % ako unos u tabeli artikala nema popust (0–50). '
            'Prazno ili 0 = bez popusta (nema −% / precrtane cijene). '
            'Flash samo na artiklima u tabeli „AI dwell artikli”.'
        ),
    )
    product_dwell_flash_seconds = models.PositiveIntegerField(
        default=120,
        verbose_name='AI dwell — trajanje tajmera (sekundi)',
        help_text='Koliko traje snizena cijena na product page (npr. 120 = 2 min). Min 30, max 3600.',
    )
    # —— AI dwell tekstovi ——
    product_dwell_tag_text = models.CharField(
        max_length=60,
        default='Ograničena ponuda',
        blank=True,
        verbose_name='AI dwell — badge tekst (detalj)',
        help_text='Npr. „Ograničena ponuda”. Prazno = sakrij badge.',
    )
    product_dwell_timer_label = models.CharField(
        max_length=60,
        default='Ističe za',
        blank=True,
        verbose_name='AI dwell — labela tajmera',
        help_text='Tekst pored odbrojavanja na product page.',
    )
    product_dwell_catalog_label = models.CharField(
        max_length=40,
        default='',
        blank=True,
        verbose_name='AI dwell — labela na kartici (katalog)',
        help_text='Opcionalno iznad cijene na listi (npr. „Akcija”). Prazno = bez labele.',
    )
    product_dwell_sale_pulse = models.BooleanField(
        default=True,
        verbose_name='AI dwell — pulsirajuća nova cijena',
        help_text='Uključeno: nova (crvena) cijena blago pulira. Isključeno: mirna boja.',
    )
    # —— AI dwell boje — product page ——
    product_dwell_boja_box = models.CharField(
        max_length=7, default='#111827', blank=True,
        verbose_name='AI dwell — pozadina boxa (detalj)',
        help_text='Hex npr. #111827',
    )
    product_dwell_boja_box2 = models.CharField(
        max_length=7, default='#1f2937', blank=True,
        verbose_name='AI dwell — pozadina boxa 2 (gradijent)',
        help_text='Druga boja gradijenta. Prazno = ista kao prva.',
    )
    product_dwell_boja_border = models.CharField(
        max_length=7, default='#e11d48', blank=True,
        verbose_name='AI dwell — rub boxa',
    )
    product_dwell_boja_accent = models.CharField(
        max_length=7, default='#e11d48', blank=True,
        verbose_name='AI dwell — accent linija / accent',
    )
    product_dwell_boja_tag_tekst = models.CharField(
        max_length=7, default='#fecdd3', blank=True,
        verbose_name='AI dwell — boja badge teksta',
    )
    product_dwell_boja_tag_bg = models.CharField(
        max_length=7, default='#e11d48', blank=True,
        verbose_name='AI dwell — boja badge pozadine',
    )
    product_dwell_boja_timer_label = models.CharField(
        max_length=7, default='#cbd5e1', blank=True,
        verbose_name='AI dwell — boja labele tajmera',
    )
    product_dwell_boja_timer_bg = models.CharField(
        max_length=7, default='#e11d48', blank=True,
        verbose_name='AI dwell — boja tajmera (pozadina)',
    )
    product_dwell_boja_timer_tekst = models.CharField(
        max_length=7, default='#ffffff', blank=True,
        verbose_name='AI dwell — boja tajmera (broj)',
    )
    product_dwell_boja_stara_cijena = models.CharField(
        max_length=7, default='#94a3b8', blank=True,
        verbose_name='AI dwell — boja stare cijene (detalj)',
    )
    product_dwell_boja_nova_cijena = models.CharField(
        max_length=7, default='#e11d48', blank=True,
        verbose_name='AI dwell — boja nove cijene (detalj)',
    )
    product_dwell_boja_nova_cijena_pulse = models.CharField(
        max_length=7, default='#ff1f4b', blank=True,
        verbose_name='AI dwell — boja pulse (vrh animacije)',
        help_text='Boja na vrhuncu pulsiranja nove cijene.',
    )
    product_dwell_boja_badge_bg = models.CharField(
        max_length=7, default='#be123c', blank=True,
        verbose_name='AI dwell — boja % badge (pozadina)',
    )
    product_dwell_boja_badge_tekst = models.CharField(
        max_length=7, default='#ffffff', blank=True,
        verbose_name='AI dwell — boja % badge (tekst)',
    )
    # —— AI dwell boje — katalog / pretraga ——
    product_dwell_boja_kartica_bg = models.CharField(
        max_length=7, default='#fff7f8', blank=True,
        verbose_name='AI dwell kartica — pozadina',
    )
    product_dwell_boja_kartica_bg2 = models.CharField(
        max_length=7, default='#ffffff', blank=True,
        verbose_name='AI dwell kartica — pozadina 2',
    )
    product_dwell_boja_kartica_border = models.CharField(
        max_length=7, default='#e11d48', blank=True,
        verbose_name='AI dwell kartica — rub',
    )
    product_dwell_boja_kartica_stara = models.CharField(
        max_length=7, default='#64748b', blank=True,
        verbose_name='AI dwell kartica — stara cijena',
    )
    product_dwell_boja_kartica_nova = models.CharField(
        max_length=7, default='#e11d48', blank=True,
        verbose_name='AI dwell kartica — nova cijena',
    )
    product_dwell_boja_kartica_badge_bg = models.CharField(
        max_length=7, default='#be123c', blank=True,
        verbose_name='AI dwell kartica — % badge pozadina',
    )
    product_dwell_boja_kartica_badge_tekst = models.CharField(
        max_length=7, default='#ffffff', blank=True,
        verbose_name='AI dwell kartica — % badge tekst',
    )
    product_dwell_boja_kartica_label = models.CharField(
        max_length=7, default='#be123c', blank=True,
        verbose_name='AI dwell kartica — boja labele',
    )
    product_dwell_artikli = models.ManyToManyField(
        'Product',
        blank=True,
        related_name='dwell_flash_sitesettings',
        verbose_name='AI dwell — artikli (ugašeno)',
        help_text='Više se ne koristi. Unosi se u tabeli ProductDwellItem (popust po artiklu).',
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
    savjetnik_aktivan = models.BooleanField(
        default=True,
        verbose_name='Ribolovački savjetnik aktivan',
        help_text=(
            'Uključeno: svi posjetioci vide „Savjeti pri kupovini” (chat). '
            'Isključeno: savjetnik se ne prikazuje na sajtu.'
        ),
    )
    # —— Chat sa kupcem (proaktivni live chat) ——
    chat_sa_kupcem_aktivan = models.BooleanField(
        default=True,
        verbose_name='Chat sa kupcem aktivan',
        help_text=(
            'Uključeno: poslije određenog vremena na sajtu automatski se otvara chat, '
            'pozdravlja kupca i nudi pomoć. Chat se ne prikazuje dok ne istekne vrijeme. '
            'Isključeno: nema proaktivnog chata (staff i dalje može imati inbox).'
        ),
    )
    chat_delay_seconds = models.PositiveIntegerField(
        default=120,
        verbose_name='Chat — vrijeme do pojave (sekunde)',
        help_text='Koliko sekundi posjetilac mora biti na sajtu prije nego chat iskoči. Npr. 120 = 2 minute.',
    )
    chat_pozdrav_poruka = models.TextField(
        blank=True,
        default=(
            'Zdravo! 👋 Dobrodošli na opremazaribolov.ba.\n\n'
            'Treba li vam pomoć ili preporuka pri kupovini?\n'
            'Pišite nam — tu smo da pomognemo.'
        ),
        verbose_name='Chat — automatska pozdrav poruka',
        help_text='Poruka koju sistem šalje kad se chat otvori. Kupac odmah može odgovoriti (bez registracije).',
    )
    chat_avatar_slika = models.ImageField(
        upload_to='site/chat/',
        blank=True,
        null=True,
        verbose_name='Chat — slika (avatar / balon)',
        help_text=(
            'Slika koja se prikazuje na chat balonu i u headeru chata kad iskoči kupcu. '
            'Preporučeno: 256×256 px, PNG/JPG (kvadrat). Ako je prazno — default ikona.'
        ),
    )
    javno_online_posjetioci = models.BooleanField(
        default=False,
        verbose_name='Javno prikaži ko je na sajtu',
        help_text=(
            'Uključeno: svi posjetioci vide koliko ljudi je trenutno na sajtu '
            '(grad / gost ili kupac — bez emaila i punog imena). '
            'Isključeno: samo superuser vidi uživo analitiku u admin panelu.'
        ),
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
        verbose_name='SEO title (početna) — fallback',
        help_text=(
            'Fallback <title> ako „SEO stranica → Početna” nije popunjena. '
            'Preporuka: 50–60 znakova, ključne riječi ispred (npr. Oprema za ribolov | Online shop BiH).'
        ),
    )
    meta_description = models.CharField(
        max_length=160, blank=True,
        verbose_name='Meta description (početna) — fallback',
        help_text=(
            'Fallback opis za Google ako „SEO stranica → Početna” nije popunjena. '
            'Preporuka: 140–160 znakova, jasan benefit + CTA (dostava, brendovi, BiH).'
        ),
    )
    og_image = models.ImageField(
        upload_to='site/', blank=True, null=True,
        verbose_name='Social share slika (OG image)',
        help_text='Preporučeno 1200×630px. Kad se link dijeli na Facebook / WhatsApp / Viber.',
    )
    # —— SEO / Google (organizacija, verifikacija, društvene mreže) ——
    seo_organizacija_naziv = models.CharField(
        max_length=120, blank=True, default='opremazaribolov.ba',
        verbose_name='Naziv trgovine (schema.org)',
        help_text='Prikazuje se u Google Knowledge / Organization JSON-LD.',
    )
    seo_email = models.EmailField(
        blank=True, default='opremazaribolov.ba@gmail.com',
        verbose_name='SEO / kontakt email',
        help_text='Za schema.org ContactPoint (customer service).',
    )
    seo_grad = models.CharField(
        max_length=80, blank=True, default='',
        verbose_name='Grad (lokalni SEO)',
        help_text='Opcionalno. Npr. Sarajevo — pomaže lokalnim upitima.',
    )
    seo_drzava = models.CharField(
        max_length=2, blank=True, default='BA',
        verbose_name='Država (ISO, npr. BA)',
        help_text='Dvoslovni kod za schema.org (BA = BiH).',
    )
    seo_facebook_url = models.URLField(
        blank=True, default='https://www.facebook.com/opremazaribolov.ba',
        verbose_name='Facebook URL',
        help_text='Puni link na Facebook stranicu (schema sameAs).',
    )
    seo_instagram_url = models.URLField(
        blank=True, default='',
        verbose_name='Instagram URL',
        help_text='Opcionalno. Puni link na Instagram profil.',
    )
    google_site_verification = models.CharField(
        max_length=120, blank=True, default='',
        verbose_name='Google Search Console — verification code',
        help_text=(
            'Samo sadržaj content=„…“ iz Google meta taga '
            '(Search Console → Ownership → HTML tag). '
            'Npr. abc123XYZ — bez cijelog <meta> taga.'
        ),
    )
    seo_title_suffix = models.CharField(
        max_length=40, blank=True, default='opremazaribolov.ba',
        verbose_name='Sufiks u title tagu',
        help_text=(
            'Dodaje se na kraju title-a kad nije već unesen (npr. „Naziv artikla | opremazaribolov.ba”). '
            'Prazno = bez sufiksa.'
        ),
    )
    politika_dostava = models.TextField(
        blank=True,
        default='Dostava brzom poštom u roku od 48h.',
        verbose_name='Uslovi dostave — tekst',
        help_text='Opcionalno. Tekst na stranici artikla ispod dugmeta korpe.',
    )
    politika_povrat = models.TextField(
        blank=True,
        default='Ukoliko je roba oštećena ili ne odgovara poručenoj, vršimo povrat.',
        verbose_name='Povrat robe — tekst',
        help_text='Opcionalno.',
    )
    politika_garancija = models.TextField(
        blank=True,
        default='Garancija na kvalitet.',
        verbose_name='Garancija — tekst',
        help_text='Opcionalno.',
    )
    badge_product_detail = models.ImageField(
        upload_to='site/', blank=True, null=True,
        verbose_name='Badge na slici artikla',
        help_text='Prikazuje se u gornjem lijevom uglu slike na stranici artikla (npr. garancija). PNG s transparentnom pozadinom.',
    )
    class NovitetiMod(models.TextChoices):
        AUTO = 'auto', 'Automatski — zadnjih 10 unesenih'
        MANUAL = 'manual', 'Ručno — odaberi do 10 artikala'

    naslov_novo = models.CharField(
        max_length=120, default='Noviteti', blank=True,
        verbose_name='Noviteti — naslov',
        help_text='Ostavite prazno da se naslov ne prikazuje.',
    )
    podnaslov_novo = models.CharField(
        max_length=200, default='', blank=True,
        verbose_name='Noviteti — podnaslov',
        help_text='Ostavite prazno da se podnaslov ne prikazuje.',
    )
    noviteti_mod = models.CharField(
        max_length=10,
        choices=NovitetiMod.choices,
        default=NovitetiMod.AUTO,
        verbose_name='Noviteti — način prikaza',
        help_text=(
            'Automatski: zadnjih 10 unesenih artikala. '
            'Ručno: artikli koje unesete u tabeli „Noviteti na početnoj” ispod (do 10).'
        ),
    )
    naslov_izdvojeno = models.CharField(
        max_length=120, default='Izdvojeni', blank=True,
        verbose_name='Izdvojeni — naslov',
        help_text='Sekcija „Izdvojeni” na početnoj. Ostavite prazno da se naslov ne prikazuje.',
    )
    podnaslov_izdvojeno = models.CharField(
        max_length=200, default='', blank=True,
        verbose_name='Izdvojeni — podnaslov',
        help_text='Ostavite prazno da se podnaslov ne prikazuje.',
    )
    naslov_akcija = models.CharField(
        max_length=120, default='Akcijska ponuda', blank=True,
        verbose_name='Akcijska ponuda — naslov',
        help_text='Sekcija sniženih artikala na početnoj. Ostavite prazno da se naslov ne prikazuje.',
    )
    podnaslov_akcija = models.CharField(
        max_length=200, default='', blank=True,
        verbose_name='Akcijska ponuda — podnaslov',
        help_text='Ostavite prazno da se podnaslov ne prikazuje.',
    )
    prikazi_akcijsku_sekciju = models.BooleanField(
        default=True,
        verbose_name='Prikaži sekciju „Akcijska ponuda”',
        help_text='Ako je uključeno, na početnoj se prikazuju artikli na akciji (kad ih ima).',
    )
    naslov_brendovi = models.CharField(
        max_length=120, default='Naši brendovi', blank=True,
        verbose_name='Brendovi — naslov',
        help_text='Naslov trake brendova na dnu početne. Prazno = sakrij naslov.',
    )
    tekst_pogledaj_sve = models.CharField(
        max_length=40, default='Pogledaj sve', blank=True,
        verbose_name='Tekst linka „Pogledaj sve”',
        help_text='Link desno od naslova sekcija proizvoda na početnoj.',
    )
    newsletter_naslov = models.CharField(
        max_length=120, default='Prijavi se na newsletter', blank=True,
        verbose_name='Newsletter — naslov',
    )
    newsletter_podnaslov = models.CharField(
        max_length=240,
        default='Budi prvi koji će saznati za nove proizvode, akcije i savjete iz svijeta ribolova.',
        blank=True,
        verbose_name='Newsletter — podnaslov',
    )
    newsletter_placeholder = models.CharField(
        max_length=80, default='Unesite e-mail adresu', blank=True,
        verbose_name='Newsletter — placeholder u polju',
    )
    newsletter_dugme = models.CharField(
        max_length=40, default='Prijavi se', blank=True,
        verbose_name='Newsletter — tekst dugmeta',
    )
    newsletter_napomena = models.CharField(
        max_length=160,
        default='Nećemo spamovati. Odjavi se u bilo kom trenutku.',
        blank=True,
        verbose_name='Newsletter — napomena ispod forme',
    )
    prikazi_newsletter = models.BooleanField(
        default=True,
        verbose_name='Prikaži newsletter box na početnoj',
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
        max_length=200, default='Vlog / Blog',
        verbose_name='Blog — naslov',
    )
    promo_bar_tekst = models.CharField(
        max_length=200,
        default='Besplatna dostava za narudžbe iznad 250 KM',
        blank=True,
        verbose_name='Promo traka — tekst',
        help_text='Tekst u gornjoj sivoj traci (iznad headera).',
    )
    promo_bar_link_tekst = models.CharField(
        max_length=80,
        default='Pridruži se sada',
        blank=True,
        verbose_name='Promo traka — link tekst',
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
    # —— Kontakt dugmad (plutajuća) — koje prikazati + boje ——
    kontakt_prikazi_whatsapp = models.BooleanField(
        default=True,
        verbose_name='Prikaži WhatsApp dugme',
    )
    kontakt_prikazi_viber = models.BooleanField(
        default=True,
        verbose_name='Prikaži Viber dugme',
    )
    kontakt_prikazi_messenger = models.BooleanField(
        default=True,
        verbose_name='Prikaži Messenger dugme',
    )
    kontakt_boja_whatsapp = models.CharField(
        max_length=7, default='#25d366', blank=True,
        verbose_name='Boja WhatsApp dugmeta',
        help_text='Hex npr. #25d366',
    )
    kontakt_boja_viber = models.CharField(
        max_length=7, default='#665cac', blank=True,
        verbose_name='Boja Viber dugmeta',
        help_text='Hex npr. #665cac',
    )
    kontakt_boja_messenger = models.CharField(
        max_length=7, default='#0084ff', blank=True,
        verbose_name='Boja Messenger dugmeta',
        help_text='Hex npr. #0084ff',
    )
    # —— Glavna CTA dugmad (boje) ——
    tekst_dugme_korpa = models.CharField(
        max_length=40, default='Dodaj u korpu', blank=True,
        verbose_name='Tekst „Dodaj u korpu”',
        help_text='Tekst na dugmetu na karticama i detail stranici.',
    )
    tekst_dugme_rasprodato = models.CharField(
        max_length=40, default='Rasprodato', blank=True,
        verbose_name='Tekst „Rasprodato”',
    )
    boja_dugme_korpa = models.CharField(
        max_length=7, default='#5BB805', blank=True,
        verbose_name='Boja „Dodaj u korpu” (kartice)',
        help_text='Zelena na karticama artikala. Hex npr. #5BB805',
    )
    boja_dugme_korpa_hover = models.CharField(
        max_length=7, default='#4fa104', blank=True,
        verbose_name='Boja „Dodaj u korpu” hover',
        help_text='Boja na prelazak miša. Hex npr. #4fa104',
    )
    boja_dugme_banner = models.CharField(
        max_length=7, default='#5BB805', blank=True,
        verbose_name='Boja dugmadi na bannerima',
        help_text='CTA na hero/grid/featured bannerima. Hex npr. #5BB805 (zelena kao na mockupu).',
    )
    boja_dugme_banner_hover = models.CharField(
        max_length=7, default='#4fa104', blank=True,
        verbose_name='Boja banner dugmadi hover',
        help_text='Hex npr. #4fa104',
    )

    class Meta:
        verbose_name = 'Podešavanja'
        verbose_name_plural = 'Podešavanja'

    def save(self, *args, **kwargs):
        self.pk = 1
        from .utils.images import (
            apply_image_processing,
            process_brand_logo,
            process_chat_avatar,
            process_product_detail_badge,
            process_site_favicon,
            process_site_logo,
        )

        if self.logo:
            apply_image_processing(self, 'logo', post_process=process_site_logo)
        if self.logo_glavni_sajt:
            apply_image_processing(self, 'logo_glavni_sajt', post_process=process_brand_logo)
        if self.favicon:
            apply_image_processing(self, 'favicon', post_process=process_site_favicon)
        if self.badge_product_detail:
            apply_image_processing(self, 'badge_product_detail', post_process=process_product_detail_badge)
        if self.chat_avatar_slika:
            apply_image_processing(self, 'chat_avatar_slika', post_process=process_chat_avatar)
        super().save(*args, **kwargs)
        try:
            from django.core.cache import cache
            cache.delete('site_settings_singleton_v1')
            cache.delete('nav_categories_tree_v1')
            cache.delete('category_ids_with_products_v1')
            cache.delete('seo_org_json_ld_v1')
            cache.delete('seo_web_json_ld_v1')
            cache.delete('showcase_brands_v1')
        except Exception:
            pass

    def format_povezani_podnaslov(self, kategorija_naziv=''):
        if not self.podnaslov_povezani:
            return ''
        return self.podnaslov_povezani.replace('{kategorija}', kategorija_naziv or '')

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        """Singleton postavki — cache da se ne radi 5–15× SELECT po requestu."""
        from django.core.cache import cache

        cache_key = 'site_settings_singleton_v1'
        obj = cache.get(cache_key)
        if obj is not None:
            return obj
        obj, _ = cls.objects.get_or_create(pk=1)
        cache.set(cache_key, obj, 120)
        return obj

    @property
    def artikala_po_stranici(self):
        return self.artikala_po_redu * 4

    @staticmethod
    def _dwell_hex(value, default):
        """Validan #RGB / #RRGGBB ili default."""
        import re
        v = (value or '').strip()
        if re.fullmatch(r'#[0-9A-Fa-f]{6}', v) or re.fullmatch(r'#[0-9A-Fa-f]{3}', v):
            return v
        return default

    def get_theme_ui(self):
        """
        Boje dugmadi (korpa, banneri, kontakt) kao CSS varijable za :root.
        """
        hx = self._dwell_hex
        cart = hx(self.boja_dugme_korpa, '#5BB805')
        cart_hover = hx(self.boja_dugme_korpa_hover, '#4fa104')
        banner = hx(self.boja_dugme_banner, '#5BB805')
        banner_hover = hx(self.boja_dugme_banner_hover, '#4fa104')
        wa = hx(self.kontakt_boja_whatsapp, '#25d366')
        viber = hx(self.kontakt_boja_viber, '#665cac')
        msg = hx(self.kontakt_boja_messenger, '#0084ff')
        css_vars = (
            f'--btn-cart:{cart};'
            f'--btn-cart-hover:{cart_hover};'
            f'--btn-banner:{banner};'
            f'--btn-banner-hover:{banner_hover};'
            f'--contact-whatsapp:{wa};'
            f'--contact-viber:{viber};'
            f'--contact-messenger:{msg};'
        )
        return {
            'css_vars': css_vars,
            'kontakt_prikazi_whatsapp': bool(self.kontakt_prikazi_whatsapp),
            'kontakt_prikazi_viber': bool(self.kontakt_prikazi_viber),
            'kontakt_prikazi_messenger': bool(self.kontakt_prikazi_messenger),
        }

    def get_dwell_ui(self):
        """
        Tekstovi + boje za AI dwell (template + CSS varijable).
        Sve se uređuje u adminu (AI prodaja).
        """
        hx = self._dwell_hex
        box = hx(self.product_dwell_boja_box, '#111827')
        box2 = hx(self.product_dwell_boja_box2, '') or box
        accent = hx(self.product_dwell_boja_accent, '#e11d48')
        border = hx(self.product_dwell_boja_border, accent)
        tag_bg = hx(self.product_dwell_boja_tag_bg, accent)
        tag_tekst = hx(self.product_dwell_boja_tag_tekst, '#fecdd3')
        timer_label = hx(self.product_dwell_boja_timer_label, '#cbd5e1')
        timer_bg = hx(self.product_dwell_boja_timer_bg, accent)
        timer_tekst = hx(self.product_dwell_boja_timer_tekst, '#ffffff')
        stara = hx(self.product_dwell_boja_stara_cijena, '#94a3b8')
        nova = hx(self.product_dwell_boja_nova_cijena, '#e11d48')
        nova_pulse = hx(self.product_dwell_boja_nova_cijena_pulse, '#ff1f4b')
        badge_bg = hx(self.product_dwell_boja_badge_bg, '#be123c')
        badge_tekst = hx(self.product_dwell_boja_badge_tekst, '#ffffff')
        card_bg = hx(self.product_dwell_boja_kartica_bg, '#fff7f8')
        card_bg2 = hx(self.product_dwell_boja_kartica_bg2, '') or '#ffffff'
        card_border = hx(self.product_dwell_boja_kartica_border, accent)
        card_stara = hx(self.product_dwell_boja_kartica_stara, '#64748b')
        card_nova = hx(self.product_dwell_boja_kartica_nova, nova)
        card_badge_bg = hx(self.product_dwell_boja_kartica_badge_bg, badge_bg)
        card_badge_tekst = hx(self.product_dwell_boja_kartica_badge_tekst, '#ffffff')
        card_label = hx(self.product_dwell_boja_kartica_label, badge_bg)

        try:
            flash_sec = int(self.product_dwell_flash_seconds or 120)
        except (TypeError, ValueError):
            flash_sec = 120
        flash_sec = max(30, min(flash_sec, 3600))

        pulse = bool(getattr(self, 'product_dwell_sale_pulse', True))
        tag_text = (self.product_dwell_tag_text or '').strip()
        timer_label_text = (self.product_dwell_timer_label or '').strip() or 'Ističe za'
        catalog_label = (self.product_dwell_catalog_label or '').strip()

        css_vars = (
            f'--dwell-box-bg:{box};'
            f'--dwell-box-bg2:{box2};'
            f'--dwell-box-border:{border};'
            f'--dwell-accent:{accent};'
            f'--dwell-tag-bg:{tag_bg};'
            f'--dwell-tag-text:{tag_tekst};'
            f'--dwell-timer-label:{timer_label};'
            f'--dwell-timer-bg:{timer_bg};'
            f'--dwell-timer-text:{timer_tekst};'
            f'--dwell-old:{stara};'
            f'--dwell-sale:{nova};'
            f'--dwell-sale-pulse:{nova_pulse};'
            f'--dwell-badge-bg:{badge_bg};'
            f'--dwell-badge-text:{badge_tekst};'
            f'--dwell-card-bg:{card_bg};'
            f'--dwell-card-bg2:{card_bg2};'
            f'--dwell-card-border:{card_border};'
            f'--dwell-card-old:{card_stara};'
            f'--dwell-card-sale:{card_nova};'
            f'--dwell-card-badge-bg:{card_badge_bg};'
            f'--dwell-card-badge-text:{card_badge_tekst};'
            f'--dwell-card-label:{card_label};'
            f'--dwell-pulse:{"1" if pulse else "0"};'
        )

        return {
            'active': bool(self.product_dwell_popup_aktivan),
            'tag_text': tag_text,
            'timer_label': timer_label_text,
            'catalog_label': catalog_label,
            'flash_seconds': flash_sec,
            'sale_pulse': pulse,
            'css_vars': css_vars,
        }

    def __str__(self):
        return 'Podešavanja'


class AIProdajaSettings(SiteSettings):
    """
    Proxy: AI prodaja + AI dwell postavke.
    U adminu se otvara preko Akcije → tip „AI prodaja / AI dwell” (nema zasebnog menija).
    """

    class Meta:
        proxy = True
        verbose_name = 'AI prodaja / AI dwell'
        verbose_name_plural = 'AI prodaja / AI dwell'


class PageSEO(models.Model):
    """
    SEO za fiksne stranice sajta (početna, korpa, o nama…).
    Sva polja su opcionalna — prazno = default / postojeći naslov u templateu.
    Za artikle, kategorije i brendove SEO se uređuje na njihovim formama.
    """

    class PageKey(models.TextChoices):
        HOME = 'home', 'Početna'
        CART = 'cart', 'Korpa'
        CHECKOUT = 'checkout', 'Narudžba (checkout)'
        ABOUT = 'about', 'O nama'
        PAYMENT = 'payment', 'Način plaćanja'
        VLOG = 'vlog', 'Blog / Vlog'
        AKCIJA = 'akcija', 'Akcija (katalog)'
        NOVITETI = 'noviteti', 'Noviteti (katalog)'
        SEARCH = 'search', 'Pretraga (rezultati)'
        LOGIN = 'login', 'Prijava'
        REGISTER = 'register', 'Registracija'
        ADVISOR = 'advisor', 'Savjetnik'
        ORDER_SUCCESS = 'order_success', 'Uspješna narudžba'

    page_key = models.CharField(
        max_length=40,
        choices=PageKey.choices,
        unique=True,
        verbose_name='Stranica',
    )
    seo_title = models.CharField(
        max_length=70,
        blank=True,
        verbose_name='SEO title',
        help_text='Opcionalno. <title> i og:title. Preporučeno 50–60 znakova.',
    )
    meta_description = models.CharField(
        max_length=160,
        blank=True,
        verbose_name='Meta description',
        help_text='Opcionalno. Opis u Google rezultatima. Do ~155–160 znakova.',
    )
    h1_naslov = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='H1 naslov',
        help_text='Opcionalno. Glavni naslov na stranici. Prazno = default tekst.',
    )
    seo_tekst_iznad = models.TextField(
        blank=True,
        verbose_name='SEO tekst iznad sadržaja / proizvoda',
        help_text='Opcionalno. Tekst iznad liste proizvoda ili glavnog sadržaja.',
    )
    seo_tekst_ispod = models.TextField(
        blank=True,
        verbose_name='SEO tekst ispod sadržaja / proizvoda',
        help_text='Opcionalno. Tekst ispod liste proizvoda ili glavnog sadržaja.',
    )
    azuriran = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'SEO stranice'
        verbose_name_plural = 'SEO stranica'
        ordering = ['page_key']

    def __str__(self):
        return self.get_page_key_display()


class ProductDwellItem(models.Model):
    """
    Artikal s ručnim flash popustom za AI dwell.
    Svaki artikal može imati svoj % (npr. 8%, 15%, 20%).
    """
    settings = models.ForeignKey(
        SiteSettings,
        on_delete=models.CASCADE,
        related_name='dwell_items',
        verbose_name='Postavke',
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        related_name='dwell_items',
        verbose_name='Artikal',
        # Samo aktivni i na stanju — autocomplete i dropdown poštuju limit_choices_to
        limit_choices_to={'aktivan': True, 'na_stanju': True},
    )
    popust = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        null=True,
        blank=True,
        verbose_name='Popust (%)',
        help_text=(
            '0–50. Prazno = default iz AI dwell postavki. '
            '0 = bez popusta (nema −% / precrtane cijene).'
        ),
    )

    class Meta:
        verbose_name = 'AI dwell artikal'
        verbose_name_plural = 'AI dwell artikli'
        ordering = ['product__naziv']
        unique_together = [('settings', 'product')]

    def __str__(self):
        if self.popust is None:
            return f'{self.product} (default %)'
        if self.popust <= 0:
            return f'{self.product} (bez popusta)'
        return f'{self.product} (−{self.popust}%)'

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.product_id:
            p = self.product
            if not getattr(p, 'aktivan', False):
                raise ValidationError({'product': 'Artikal mora biti aktivan.'})
            if not getattr(p, 'na_stanju', False):
                raise ValidationError({
                    'product': 'Ne možeš dodati artikal koji nije na stanju.',
                })
        if self.popust is not None:
            if self.popust < 0:
                raise ValidationError({'popust': 'Popust ne može biti negativan.'})
            if self.popust > 50:
                raise ValidationError({'popust': 'Maksimalni popust je 50%.'})


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
        verbose_name='SEO title',
        help_text='Opcionalno. Ako ostaviš prazno koristi se naziv kategorije.',
    )
    meta_description = models.CharField(
        max_length=160, blank=True,
        verbose_name='Meta description',
        help_text='Opcionalno. Kratak opis za Google i društvene mreže.',
    )
    h1_naslov = models.CharField(
        max_length=200, blank=True,
        verbose_name='H1 naslov',
        help_text='Opcionalno. Prazno = naziv kategorije.',
    )
    seo_tekst_iznad = models.TextField(
        blank=True,
        verbose_name='SEO tekst iznad proizvoda',
        help_text='Opcionalno. Tekst iznad liste artikala.',
    )
    seo_tekst_ispod = models.TextField(
        blank=True,
        verbose_name='SEO tekst ispod proizvoda',
        help_text='Opcionalno. Tekst ispod liste artikala.',
    )
    search_tagovi = models.TextField(
        blank=True,
        verbose_name='Search tagovi',
        help_text=(
            'Samo za podkategorije. Neograničen broj tagova — odvoji zarezom ili novim redom. '
            'Duga rečenica je JEDAN tag (razmaci ostaju), npr. „stap za pecanje sarana”. '
            'Zarez tek dijeli sljedeći tag. Vrijedi za artikle u ovoj podkategoriji i podnivoima.'
        ),
    )

    class Meta:
        verbose_name = 'Kategorija'
        verbose_name_plural = 'Kategorije'
        ordering = ['redoslijed', 'naziv']

    @staticmethod
    def normalize_search_tagovi(raw):
        """
        Normalizuj tagove (zarez / novi red / | / ;).
        Razmaci unutar taga OSTAJU — duga rečenica je jedan tag.
        Bez limita broja tagova, bez skraćivanja.
        """
        if not raw:
            return ''
        text = str(raw).replace('\r\n', '\n').replace('\r', '\n')
        text = text.replace(';', '\n').replace('|', '\n')
        seen = set()
        out = []
        for line in text.split('\n'):
            for part in line.split(','):
                tag = re.sub(r'\s+', ' ', part.strip())
                if not tag:
                    continue
                key = tag.casefold()
                if key in seen:
                    continue
                seen.add(key)
                out.append(tag)
        return ', '.join(out)

    def search_tagovi_list(self):
        if not self.search_tagovi:
            return []
        return [
            t.strip()
            for t in self.search_tagovi.replace(';', ',').split(',')
            if t.strip()
        ]

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
        # Search tagovi važe samo za podkategorije (imaju roditelja)
        if not self.roditelj_id:
            self.search_tagovi = ''
        elif self.search_tagovi:
            self.search_tagovi = self.normalize_search_tagovi(self.search_tagovi)
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
        # Unique slug uvijek (slugify više naziva može dati isti slug)
        base = (self.slug and self.slug.strip()) or slugify(self.naziv) or 'tag'
        base = slugify(base) or 'tag'
        slug = base
        counter = 1
        while Tag.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f'{base}-{counter}'
            counter += 1
        self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        if self.roditelj:
            return f'{self.roditelj.naziv} → {self.naziv}'
        return self.naziv

    @classmethod
    def get_or_create_by_name(cls, name):
        """
        Pronađi tag po nazivu (case-insensitive) ili kreiraj novi
        s jedinstvenim slugom.
        """
        name = (name or '').strip()
        if not name:
            raise ValueError('Prazan naziv taga')
        # max_length=50 na naziv
        name = name[:50]
        existing = cls.objects.filter(naziv__iexact=name).first()
        if existing:
            return existing, False
        # Ako postoji isti slug od drugog naziva — save() rješava unique
        tag = cls(naziv=name)
        tag.save()
        return tag, True

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
    meta_title = models.CharField(
        max_length=70, blank=True,
        verbose_name='SEO title',
        help_text='Opcionalno. Prazno = naziv brenda.',
    )
    meta_description = models.CharField(
        max_length=160, blank=True,
        verbose_name='Meta description',
        help_text='Opcionalno. Opis za Google kad se filtrira po brendu.',
    )
    h1_naslov = models.CharField(
        max_length=200, blank=True,
        verbose_name='H1 naslov',
        help_text='Opcionalno. Prazno = naziv brenda.',
    )
    seo_tekst_iznad = models.TextField(
        blank=True,
        verbose_name='SEO tekst iznad proizvoda',
        help_text='Opcionalno.',
    )
    seo_tekst_ispod = models.TextField(
        blank=True,
        verbose_name='SEO tekst ispod proizvoda',
        help_text='Opcionalno.',
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
    slika = models.ImageField(
        upload_to='banners/',
        blank=True,
        null=True,
        verbose_name='Slika (desktop)',
        help_text='Hero: 1920×640 px (3:1). Ostali tipovi po potrebi.',
    )
    slika_mobilna = models.ImageField(
        upload_to='banners/mobile/',
        blank=True,
        null=True,
        verbose_name='Slika (mobilni)',
        help_text=(
            'Opcionalno — SAMO na telefonu (≤768px). '
            'Preporuka: JPG 720×900 (4:5), max ~2 MB. '
            'Server automatski smanjuje; prevelike slike mogu srušiti free hosting. '
            'Ako je prazno — koristi se desktop slika.'
        ),
    )
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
        if self.slika_mobilna:
            from functools import partial
            from .utils.images import apply_image_processing, process_banner_image_for_admin
            # hero_mobile: uža/viša obrada za telefone
            mobile_tip = 'hero_mobile' if self.tip == self.BannerType.HERO else self.tip
            apply_image_processing(
                self,
                'slika_mobilna',
                post_process=partial(process_banner_image_for_admin, tip=mobile_tip),
            )
        super().save(*args, **kwargs)

    def get_link_href(self):
        href = None
        if self.link:
            raw = (self.link or '').strip()
            if raw.startswith(('http://', 'https://')):
                # Apsolutni URL: ako je naš domen / localhost → koristi samo putanju
                # (inače svi baneri s 127.0.0.1 ili starim domenom vode krivo)
                from urllib.parse import urlparse
                from django.conf import settings as dj_settings
                parsed = urlparse(raw)
                host = (parsed.hostname or '').lower()
                site_host = ''
                try:
                    site_host = (urlparse(getattr(dj_settings, 'SITE_URL', '') or '').hostname or '').lower()
                except Exception:
                    site_host = ''
                local_hosts = {
                    'localhost', '127.0.0.1', '0.0.0.0',
                    'www.opremazaribolov.ba', 'opremazaribolov.ba',
                }
                if site_host:
                    local_hosts.add(site_host)
                    if site_host.startswith('www.'):
                        local_hosts.add(site_host[4:])
                    else:
                        local_hosts.add(f'www.{site_host}')
                if host in local_hosts or host.endswith('.onrender.com'):
                    path = parsed.path or '/'
                    if parsed.query:
                        path = f'{path}?{parsed.query}'
                    if parsed.fragment:
                        path = f'{path}#{parsed.fragment}'
                    href = path
                else:
                    href = raw
            elif raw.startswith('/'):
                href = raw
            elif raw.startswith('?'):
                href = f'/{raw}'
            else:
                href = f'/{raw.strip("/")}/'
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


class HomeTrustItem(models.Model):
    """USP traka ispod hero banera (Brza dostava, Sigurna kupovina…)."""

    class Icon(models.TextChoices):
        TRUCK = 'truck', 'Dostava / kamion'
        SHIELD = 'shield', 'Štit / sigurnost'
        BADGE = 'badge', 'Značka / original'
        SUPPORT = 'support', 'Podrška / slušalice'
        GIFT = 'gift', 'Poklon'
        STAR = 'star', 'Zvijezda'
        CHECK = 'check', 'Kvačica'
        TAG = 'tag', 'Cjenovni tag'

    postavke = models.ForeignKey(
        SiteSettings,
        on_delete=models.CASCADE,
        related_name='trust_stavke',
        default=1,
        editable=False,
    )
    naslov = models.CharField(max_length=80, verbose_name='Naslov')
    podnaslov = models.CharField(max_length=120, blank=True, verbose_name='Podnaslov')
    ikona = models.CharField(
        max_length=20,
        choices=Icon.choices,
        default=Icon.CHECK,
        verbose_name='Ikona',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Trust stavka (početna)'
        verbose_name_plural = 'Trust traka (početna)'
        ordering = ['redoslijed', 'id']

    def __str__(self):
        return self.naslov


class HomePromoCard(models.Model):
    """Tamne promo kartice na početnoj (Besplatna dostava, Vjernost, Poklon…)."""

    class Icon(models.TextChoices):
        TRUCK = 'truck', 'Dostava / kamion'
        PERCENT = 'percent', 'Popust %'
        GIFT = 'gift', 'Poklon / kartice'
        TAG = 'tag', 'Akcije / tag'
        STAR = 'star', 'Zvijezda / vjernost'
        SHIELD = 'shield', 'Štit'
        SUPPORT = 'support', 'Podrška'

    postavke = models.ForeignKey(
        SiteSettings,
        on_delete=models.CASCADE,
        related_name='promo_kartice',
        default=1,
        editable=False,
    )
    naslov = models.CharField(
        max_length=80,
        verbose_name='Naslov',
        help_text='Zeleni naslov. Za 2 reda stavi Enter (npr. BESPLATNA ↵ DOSTAVA).',
    )
    podnaslov = models.CharField(max_length=160, blank=True, verbose_name='Podnaslov')
    badge = models.CharField(
        max_length=40,
        blank=True,
        verbose_name='Badge / isticanje',
        help_text=(
            'Npr. „DO 10%”, „50 KM”, „150 KM”. '
            'Za dostavu (truck) prikazuje se kao veliki broj ispod teksta; '
            'za pečat/poklon ide u ikonu ako nema uploadane slike.'
        ),
    )
    slika = models.ImageField(
        upload_to='promo/',
        blank=True,
        null=True,
        verbose_name='Slika (desno na kartici)',
        help_text=(
            'Tvoja slika umjesto ugrađene ikone. '
            'Preporučeno: 200×200 px (ili 240×160), PNG s transparentnom pozadinom, zelena grafika. '
            'Ako je prazno — koristi se SVG ikona (polje „Ikona”).'
        ),
    )
    ikona = models.CharField(
        max_length=20,
        choices=Icon.choices,
        default=Icon.STAR,
        verbose_name='Ikona (fallback)',
        help_text='Koristi se samo ako nema uploadane slike.',
    )
    boja_naslova = models.CharField(
        max_length=7,
        default='#5BB805',
        blank=True,
        verbose_name='Boja naslova',
        help_text='Hex npr. #5BB805 (zelena) ili #ffffff (bijela).',
    )
    boja_podnaslova = models.CharField(
        max_length=7,
        default='#FFFFFF',
        blank=True,
        verbose_name='Boja podnaslova',
        help_text='Hex npr. #FFFFFF ili #CCCCCC.',
    )
    boja_isticanja = models.CharField(
        max_length=7,
        default='#FFFFFF',
        blank=True,
        verbose_name='Boja isticanja (npr. 150 KM)',
        help_text='Boja velikog broja ispod teksta (dostava). Hex npr. #FFFFFF.',
    )
    link = models.CharField(
        max_length=300,
        blank=True,
        verbose_name='Link (URL ili putanja)',
        help_text='Npr. /?akcija=1 ili https://… Prazno = bez linka.',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Promo kartica (početna)'
        verbose_name_plural = 'Promo kartice (početna)'
        ordering = ['redoslijed', 'id']

    @staticmethod
    def _hex_color(value, default):
        import re
        v = (value or '').strip()
        if re.fullmatch(r'#[0-9A-Fa-f]{6}', v) or re.fullmatch(r'#[0-9A-Fa-f]{3}', v):
            if len(v) == 4:
                return '#' + ''.join(c * 2 for c in v[1:])
            return v
        return default

    def css_boja_naslova(self):
        return self._hex_color(self.boja_naslova, '#5BB805')

    def css_boja_podnaslova(self):
        return self._hex_color(self.boja_podnaslova, '#FFFFFF')

    def css_boja_isticanja(self):
        return self._hex_color(self.boja_isticanja, '#FFFFFF')

    def save(self, *args, **kwargs):
        if self.slika:
            from .utils.images import apply_image_processing, process_promo_card_image
            apply_image_processing(
                self, 'slika', post_process=process_promo_card_image,
            )
        super().save(*args, **kwargs)

    def get_link_href(self):
        url = (self.link or '').strip()
        if not url:
            return ''
        if url.startswith(('http://', 'https://', '/', '?', '#')):
            return url
        return f'/{url.lstrip("/")}'

    def __str__(self):
        return self.naslov


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
        verbose_name = 'Izdvojeni artikal (početna)'
        verbose_name_plural = 'Izdvojeni artikli (početna)'
        ordering = ['redoslijed', 'id']

    def __str__(self):
        return self.artikal.naziv


class HomeNovoProduct(models.Model):
    """Ručno odabrani noviteti na početnoj (kad je noviteti_mod = manual)."""
    postavke = models.ForeignKey(
        SiteSettings,
        on_delete=models.CASCADE,
        related_name='noviteti_artikli',
        default=1,
        editable=False,
    )
    artikal = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        related_name='noviteti_na_pocetnoj',
        verbose_name='Postojeći artikal',
        limit_choices_to={'aktivan': True},
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Novitet (početna)'
        verbose_name_plural = 'Noviteti na početnoj (ručno)'
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


class HomeBrandShowcase(models.Model):
    """Brend sekcija na početnoj — artikli brenda u slide/karuselu (kao Noviteti / HIT)."""
    postavke = models.ForeignKey(
        SiteSettings,
        on_delete=models.CASCADE,
        related_name='brendovi_na_pocetnoj',
        default=1,
        editable=False,
    )
    brend = models.ForeignKey(
        'Brand',
        on_delete=models.CASCADE,
        related_name='pocetna_sekcije',
        verbose_name='Brend',
    )
    naslov = models.CharField(
        max_length=120,
        blank=True,
        verbose_name='Naslov sekcije',
        help_text='Prazno = naziv brenda.',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Brend na početnoj (slide)'
        verbose_name_plural = 'Brendovi na početnoj (slide karusel)'
        ordering = ['redoslijed', 'id']
        unique_together = [('postavke', 'brend')]

    def display_title(self):
        return (self.naslov or '').strip() or self.brend.naziv

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
    kratki_opis = models.CharField(
        max_length=220,
        blank=True,
        verbose_name='Kratki opis (početna)',
        help_text='Jedna rečenica ispod naslova na početnoj. Prazno = prvih ~120 znakova iz sadržaja.',
    )
    objavljeno = models.DateField(
        null=True,
        blank=True,
        verbose_name='Datum objave',
        help_text='Prikazuje se na kartici vloga (npr. 05.08.2025). Prazno = datum kreiranja.',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')
    kreirano = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = 'Vlog'
        verbose_name_plural = 'Vlogovi'
        ordering = ['redoslijed', '-id']

    @property
    def display_date(self):
        if self.objavljeno:
            return self.objavljeno
        if self.kreirano:
            return timezone.localtime(self.kreirano).date()
        return None

    def short_teaser(self, max_len=140):
        text = (self.kratki_opis or '').strip()
        if not text:
            from django.utils.html import strip_tags
            text = strip_tags(self.sadrzaj or '').strip()
        if len(text) <= max_len:
            return text
        trimmed = text[: max_len - 1]
        if ' ' in trimmed:
            trimmed = trimmed.rsplit(' ', 1)[0]
        return f'{trimmed}…'

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
        # Aktivni tipovi (admin)
        BUNDLE = 'bundle', 'Pop-up bundle'
        QTY_DEAL = 'qty_deal', 'Kupi više (količinski %)'
        PONUDA = 'ponuda', '+ Ponuda'
        AI_PRODAJA = 'ai_prodaja', 'AI prodaja / AI dwell'
        # Zastarjeli (zadržani zbog postojećih redova; više se ne nude)
        SLIKA = 'slika', 'Pop-up + slika (zastarjelo)'
        TIMER = 'timer', 'Akcija + tajmer (zastarjelo)'
        X_PLUS_1 = 'x_plus_1', 'X+1 prodaja (zastarjelo)'
        USLOV = 'uslov', 'Uslov prodaja (zastarjelo)'
        KORPA_NUDJENJE = 'korpa_nudjenje', 'Korpa nudjenje (zastarjelo)'
        GRATIS = 'gratis', '+ Gratis (zastarjelo)'

    # Tipovi u listi Akcije (admin)
    ACTIVE_TIPS = (Tip.BUNDLE, Tip.QTY_DEAL, Tip.PONUDA, Tip.AI_PRODAJA)
    # Samo ovi idu u popup queue na sajtu (site_popup kašnjenje)
    # Kupi više (qty_deal) NIJE page popup — iskače tek kad kupac doda artikal u korpu
    POPUP_TIPS = (Tip.BUNDLE,)
    # Add-to-cart cross-sell (modal DA/NE)
    CART_OFFER_TIPS = (Tip.PONUDA, Tip.GRATIS)

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
        default=Tip.BUNDLE,
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
        verbose_name='1. Trigger artikal',
        help_text=(
            '+ Ponuda: artikal koji kupac dodaje u korpu — tada iskače popup. '
            'Kupi više: artikal na koji važi količinski popust. '
            'Bundle: samo ako je trigger „odabrani trigger artikal”.'
        ),
    )
    gratis_artikal = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='akcije_gratis',
        verbose_name='3. Ponuda artikal (popup)',
        help_text=(
            '+ Ponuda: artikal koji se nudi u popupu (AI dwell stil) '
            'nakon dodavanja triggera. Obavezno za + Ponuda.'
        ),
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
        verbose_name='2. Popust (%) — opcionalno',
        help_text=(
            '+ Ponuda: % snizenja na ponuđeni artikal; prazno = regularna cijena. '
            'Pop-up bundle: % na cijeli set (ako linija nema svoj %). '
            'Kupi više: % unosi se u polja „Kupi 2/3/… komada”, ne ovdje.'
        ),
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
        help_text=(
            '0 = odmah. Ne vrijedi za X+1 (samo korpa). '
            'Ne vrijedi za „Kupi više” — taj se prikazuje samo na stranici odabranog artikla, bez kašnjenja.'
        ),
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
        """Samo Pop-up bundle i Kupi više — AI prodaja/dwell nije popup akcija."""
        return self.tip in self.POPUP_TIPS

    def bundle_line_rows(self):
        """
        Linije seta s količinom i opcionalnim % po artiklu.
        Preferira bundle_lines; fallback na stari M2M (qty=1).
        """
        if self.tip != self.Tip.BUNDLE or not self.pk:
            return []
        lines = list(
            self.bundle_lines.select_related('product')
            .filter(product__aktivan=True)
            .order_by('redoslijed', 'id')
        )
        if lines:
            return [
                {
                    'product': line.product,
                    'quantity': max(1, int(line.quantity or 1)),
                    'line': line,
                    'popust_postotak': line.effective_discount_percent(self),
                }
                for line in lines
                if line.product_id
            ]
        # Legacy M2M
        default_pct = self.popust_postotak
        return [
            {
                'product': p,
                'quantity': 1,
                'line': None,
                'popust_postotak': default_pct,
            }
            for p in self.bundle_artikli.filter(aktivan=True).order_by('naziv', 'id')
        ]

    def bundle_unit_count(self):
        """Ukupan broj komada u setu (suma količina)."""
        return sum(int(r['quantity']) for r in self.bundle_line_rows())

    def bundle_products(self):
        """
        Aktivni artikli u setu — prošireno po količini
        (isti artikal 2× → [A, A] za korpu).
        """
        if self.tip != self.Tip.BUNDLE:
            return []
        expanded = []
        for row in self.bundle_line_rows():
            for _ in range(row['quantity']):
                expanded.append(row['product'])
        return expanded

    def bundle_display_items(self):
        """
        Kartice za popup — 1 kartica po liniji (isti artikal qty 2 = jedna slika + ×2).

        Izuzetak: ukupno tačno 2 komada u setu — uvijek 2 kartice (artikal + artikal),
        nikad jedna kartica sa ×2, da se polje vizuelno popuni.
        """
        items = []
        for row in self.bundle_line_rows():
            p = row['product']
            qty = max(1, int(row['quantity'] or 1))
            pct = row.get('popust_postotak')
            cijene = self.bundle_cijene_za_artikal(p, popust_postotak=pct)
            bazna = cijene['bazna'] if cijene else p.prikazna_cijena
            snizena = cijene['snizena'] if cijene else p.prikazna_cijena
            has_discount = bool(cijene and cijene['snizena'] < cijene['bazna'])
            usteda_one = (bazna - snizena) if has_discount else Decimal('0')
            pct_label = ''
            if pct is not None:
                try:
                    pct_d = Decimal(str(pct))
                    pct_label = str(int(pct_d)) if pct_d == int(pct_d) else str(pct_d)
                except Exception:
                    pct_label = str(pct)
            items.append({
                'product': p,
                'quantity': qty,
                'bazna': bazna,
                'snizena': snizena,
                'line_bazna': (bazna * qty).quantize(Decimal('0.01')) if bazna is not None else bazna,
                'line_snizena': (snizena * qty).quantize(Decimal('0.01')) if snizena is not None else snizena,
                'has_discount': has_discount,
                'usteda': (usteda_one * qty).quantize(Decimal('0.01')),
                'pct_label': pct_label,
                'popust_postotak': pct,
            })

        unit_count = sum(int(i.get('quantity') or 1) for i in items)
        # Samo 2 komada u setu: proširi qty 2 → dvije kartice (A + A), ne ×2
        if unit_count == 2 and any(int(i.get('quantity') or 1) > 1 for i in items):
            expanded = []
            for i in items:
                qty = max(1, int(i.get('quantity') or 1))
                if qty <= 1:
                    expanded.append(i)
                    continue
                unit_usteda = (
                    (i['bazna'] - i['snizena']).quantize(Decimal('0.01'))
                    if i.get('has_discount') and i.get('bazna') is not None and i.get('snizena') is not None
                    else Decimal('0')
                )
                for _ in range(qty):
                    expanded.append({
                        **i,
                        'quantity': 1,
                        'line_bazna': i['bazna'],
                        'line_snizena': i['snizena'],
                        'usteda': unit_usteda,
                    })
            return expanded
        return items

    def bundle_pricing_summary(self):
        """
        Ukupno „inače” vs cijena seta — za vizuelnu usporedbu
        (precrtana suma pojedinačnih cijena vs zelena cijena seta).
        """
        items = self.bundle_display_items()
        unit_count = sum(int(i.get('quantity') or 1) for i in items)
        if unit_count < 2 and len(items) < 1:
            return None
        if unit_count < 2:
            return None
        total_bazna = sum(
            (i['bazna'] or Decimal('0')) * int(i.get('quantity') or 1) for i in items
        )
        total_snizena = sum(
            (i['snizena'] or Decimal('0')) * int(i.get('quantity') or 1) for i in items
        )
        usteda = total_bazna - total_snizena
        if usteda < 0:
            usteda = Decimal('0')
        # Ribbon: jedan % ako svi isti, inače „do X%” ili set-level
        pcts = []
        for i in items:
            raw = i.get('popust_postotak')
            if raw is None:
                continue
            try:
                pcts.append(Decimal(str(raw)))
            except Exception:
                continue
        pct_label = ''
        if pcts:
            uniq = {p.quantize(Decimal('0.01')) for p in pcts}
            if len(uniq) == 1:
                p = next(iter(uniq))
                pct_label = str(int(p)) if p == int(p) else str(p)
            else:
                pmax = max(uniq)
                pct_label = f'do {int(pmax) if pmax == int(pmax) else pmax}'
        elif self.popust_postotak is not None:
            pct = self.popust_postotak
            pct_label = str(int(pct)) if pct == int(pct) else str(pct)
        per_product = any(
            getattr(r.get('line'), 'popust_postotak', None) is not None
            for r in self.bundle_line_rows()
        )
        return {
            'count': unit_count,
            'line_count': len(items),
            'total_bazna': total_bazna.quantize(Decimal('0.01')),
            'total_snizena': total_snizena.quantize(Decimal('0.01')),
            'usteda': usteda.quantize(Decimal('0.01')),
            'pct_label': pct_label,
            'has_discount': usteda > 0,
            'per_product_discount': per_product,
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
            slug = m.group(1)
            if self.bundle_lines.filter(product__slug=slug, product__aktivan=True).exists():
                return True
            return self.bundle_artikli.filter(slug=slug, aktivan=True).exists()

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
        if self.tip == self.Tip.PONUDA:
            # + Ponuda nije site popup queue — modal na dodaj u korpu
            return False
        if self.tip == self.Tip.GRATIS:
            if not self.gratis_popup:
                return False
            if not self.artikal_id or not self.gratis_artikal_id or self.popust_postotak is None:
                return False
        elif self.tip == self.Tip.BUNDLE:
            # Mora postojati % na setu ili na barem jednoj liniji
            has_set_pct = self.popust_postotak is not None
            has_line_pct = (
                self.pk
                and self.bundle_lines.filter(popust_postotak__isnull=False).exists()
            )
            if not has_set_pct and not has_line_pct:
                return False
            if self.pk and self.bundle_unit_count() < 2:
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
        elif self.tip == self.Tip.QTY_DEAL:
            # Ne ide u site_popup queue — modal pri dodavanju u korpu
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
        """Snižena cijena za + Ponuda / + Gratis (% na ponuđeni artikal)."""
        if (
            self.tip not in (self.Tip.PONUDA, self.Tip.GRATIS)
            or self.popust_postotak is None
            or not self.jos_traje()
            or not self.gratis_artikal_id
            or product.pk != self.gratis_artikal_id
        ):
            return None
        bazna = variation.prikazna_cijena if variation else product.prikazna_cijena
        return _izracunaj_akcijsku_od_postotka(bazna, self.popust_postotak)

    def gratis_cijene_za_prikaz(self):
        """Originalna i (opcionalno) snižena cijena ponuđenog artikla za pop-up."""
        if self.tip not in (self.Tip.PONUDA, self.Tip.GRATIS) or not self.gratis_artikal_id:
            return None
        artikal = self.gratis_artikal
        if artikal is None:
            return None
        bazna = artikal.prikazna_cijena
        if self.popust_postotak is None:
            return {
                'bazna': bazna,
                'snizena': bazna,
                'pct': None,
            }
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

    def bundle_cijene_za_artikal(self, product, *, popust_postotak=None):
        """Originalna i snižena cijena artikla u Pop-up bundle setu (% po liniji ili set)."""
        if self.tip != self.Tip.BUNDLE or not product:
            return None
        if self.pk:
            in_lines = self.bundle_lines.filter(product_id=product.pk).exists()
            in_m2m = self.bundle_artikli.filter(pk=product.pk).exists()
            if not in_lines and not in_m2m:
                return None
        pct = popust_postotak if popust_postotak is not None else self.popust_postotak
        if pct is None:
            return None
        bazna = product.prikazna_cijena
        snizena = _izracunaj_akcijsku_od_postotka(bazna, pct)
        if snizena is None:
            return None
        return {
            'bazna': bazna,
            'snizena': snizena,
            'pct': pct,
        }

    def qty_deal_tiers(self):
        """
        Količinski tierovi: npr. 2 kom → -10%, 3 kom → -20%.
        Vraća listu dict-ova sortirano po količini.
        """
        if self.tip != self.Tip.QTY_DEAL or not self.pk:
            return []
        rows = []
        for tier in self.qty_tiers.order_by('quantity', 'redoslijed', 'id'):
            try:
                qty = int(tier.quantity or 0)
            except (TypeError, ValueError):
                continue
            if qty < 2 or tier.popust_postotak is None:
                continue
            rows.append({
                'id': tier.pk,
                'quantity': qty,
                'popust_postotak': tier.popust_postotak,
                'tier': tier,
            })
        return rows

    def qty_deal_trigger_matches(self, request):
        """
        Kupi više: samo na stranici odabranog artikla.
        Ne prikazuj širom sajta nakon kašnjenja — trigger je taj artikal.
        """
        if self.tip != self.Tip.QTY_DEAL or not self.artikal_id:
            return False
        if request is None:
            return False
        try:
            path = (request.path or '').rstrip('/') or '/'
        except Exception:
            path = ''
        slug = ''
        if self.artikal:
            slug = (self.artikal.slug or '').strip()
        if not slug:
            return False
        return path == f'/artikal/{slug}'

    def qty_deal_display_options(self):
        """
        Opcije za modal „Kupi više”: samo tierovi 2/3/… s % (bez 1 kom —
        kupac je već dodao artikal u korpu s odabranom količinom).
        """
        product = self.artikal
        if not product or self.tip != self.Tip.QTY_DEAL:
            return []
        bazna = product.prikazna_cijena
        options = []
        for row in self.qty_deal_tiers():
            pct = row['popust_postotak']
            snizena = _izracunaj_akcijsku_od_postotka(bazna, pct)
            if snizena is None:
                continue
            qty = row['quantity']
            try:
                pct_label = int(pct) if pct == int(pct) else pct
            except (TypeError, ValueError):
                pct_label = pct
            options.append({
                'id': row['id'],
                'quantity': qty,
                'popust_postotak': pct,
                'pct_label': pct_label,
                'unit_bazna': bazna,
                'unit_snizena': snizena,
                'line_bazna': (bazna * qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                'line_snizena': (snizena * qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                'usteda': ((bazna - snizena) * qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                'is_single': False,
            })
        return options

    def qty_deal_best_option(self):
        # Najveća ušteda u KM (za „UŠTEDI DO”) — među tierovima 2+
        opts = [
            o for o in self.qty_deal_display_options()
            if int(o.get('quantity') or 0) >= 2
        ]
        if not opts:
            return None
        return max(
            opts,
            key=lambda o: (
                o.get('usteda') or 0,
                o.get('popust_postotak') or 0,
                int(o.get('quantity') or 0),
            ),
        )

    def get_link_href(self):
        if self.artikal_id and self.tip in {
            self.Tip.TIMER, self.Tip.USLOV, self.Tip.QTY_DEAL,
        }:
            return self.artikal.get_absolute_url()
        if self.link_dugmeta:
            if self.link_dugmeta.startswith(('http://', 'https://', '/')):
                return self.link_dugmeta
            return f'/{self.link_dugmeta.strip("/")}/'
        return reverse('register')

    def __str__(self):
        status = 'aktivan' if self.aktivan else 'neaktivan'
        return f'{self.naziv} ({self.get_tip_display()}, {status})'


class AkcijaBundleLine(models.Model):
    """
    Stavka Pop-up bundle seta s količinom i opcionalnim % po artiklu.
    Isti artikal qty 2+ = jedna slika ×2 u popup-u.
    """
    akcija = models.ForeignKey(
        Akcija,
        on_delete=models.CASCADE,
        related_name='bundle_lines',
        verbose_name='Akcija',
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        related_name='akcija_bundle_lines',
        verbose_name='Artikal',
        # Samo aktivni i na stanju — autocomplete / dropdown
        limit_choices_to={'aktivan': True, 'na_stanju': True},
    )
    quantity = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Količina u setu',
        help_text='Npr. 2 = isti artikal ×2 (1+1). U popup-u: jedna slika + ×2.',
    )
    popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust % (samo ovaj artikal)',
        help_text=(
            'Opcionalno. Ako uneseš — važi samo za ovaj artikal. '
            'Prazno = koristi % kompletnog seta iz akcije.'
        ),
    )
    redoslijed = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Redoslijed',
    )

    class Meta:
        verbose_name = 'Bundle stavka'
        verbose_name_plural = 'Bundle stavke'
        ordering = ['redoslijed', 'id']

    def effective_discount_percent(self, akcija=None):
        """% za ovu liniju: linija > set."""
        if self.popust_postotak is not None:
            return self.popust_postotak
        akcija = akcija or self.akcija
        return getattr(akcija, 'popust_postotak', None)

    def __str__(self):
        naziv = self.product.naziv if self.product_id else '?'
        pct = self.popust_postotak
        extra = f', -{pct}%' if pct is not None else ''
        return f'{naziv} ×{self.quantity}{extra}'


class AkcijaQtyTier(models.Model):
    """
    Količinski popust: kupi N komada istog artikla za -%.
    Npr. 2 → -10%, 3 → -20%. Nije set različitih artikala — samo više komada.
    """
    akcija = models.ForeignKey(
        Akcija,
        on_delete=models.CASCADE,
        related_name='qty_tiers',
        verbose_name='Akcija',
    )
    quantity = models.PositiveSmallIntegerField(
        verbose_name='Kupi (komada)',
        help_text='Minimalno 2. Npr. 2 = kupi 2 za taj %.',
    )
    popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        verbose_name='Popust (%)',
        help_text='Postotak popusta po komadu kad kupac uzme ovu količinu.',
    )
    redoslijed = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Redoslijed',
    )

    class Meta:
        verbose_name = 'Količinski popust'
        verbose_name_plural = 'Količinski popusti (2, 3…)'
        ordering = ['quantity', 'redoslijed', 'id']
        unique_together = [('akcija', 'quantity')]

    def __str__(self):
        return f'Kupi {self.quantity} → -{self.popust_postotak}%'


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
    pakovanje_komada = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name='Pakovanje (komada)',
        help_text=(
            'Ako se prodaje u pakovanju (ne po komadu): unesi broj komada u pakovanju '
            '(npr. 9). Prazno = cijena je po komadu. Slika može biti jednog artikla — '
            'kupac vidi da je cijena za pakovanje od N komada.'
        ),
    )
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
    je_novitet = models.BooleanField(
        default=False,
        verbose_name='Noviteti',
        help_text=(
            'Uključeno: zeleni pulsirajući natpis „NOVITETI” na artiklu '
            'i prikaz u karuselu Noviteti na početnoj.'
        ),
    )
    je_hit = models.BooleanField(
        default=False,
        verbose_name='HIT ponuda / Izdvojeno',
        help_text=(
            'Uključeno: crveni pulsirajući natpis „HIT PONUDA” na artiklu '
            'i prikaz u karuselu Izdvojeni artikli na početnoj.'
        ),
    )

    class PrioritetLagera(models.IntegerChoices):
        NORMAL = 0, 'Normalno'
        FAVORIZUJ = 1, 'Favorizuj'
        HIT = 2, 'Hit redukovanje lagera'

    prioritet_lagera = models.PositiveSmallIntegerField(
        choices=PrioritetLagera.choices,
        default=PrioritetLagera.NORMAL,
        db_index=True,
        verbose_name='Redukovanje lagera',
        help_text=(
            'Prioritet među relevantnim rezultatima (pretraga, kategorija, preporuke). '
            'Nikad ne gura nerelevantne artikle. '
            'Normalno = bez boosta; Favorizuj = blago; '
            'Hit redukovanje lagera = maksimalni prioritet.'
        ),
    )
    proizvedeno_u_japanu = models.BooleanField(
        default=False, verbose_name='Proizvedeno u Japanu',
    )
    aktivan = models.BooleanField(default=True)
    odoo_template_id = models.PositiveIntegerField(
        blank=True, null=True, unique=True, verbose_name='Odoo template ID',
    )
    magacin_sync_at = models.DateTimeField(
        blank=True, null=True, db_index=True,
        verbose_name='Magacin sync',
        help_text='Kad je artikal zadnji put povučen iz Odoa u Magacin.',
    )
    meta_title = models.CharField(
        max_length=70, blank=True,
        verbose_name='SEO title',
        help_text='Opcionalno — ostavi prazno za automatski (naziv artikla).',
    )
    meta_description = models.CharField(
        max_length=160, blank=True,
        verbose_name='Meta description',
        help_text='Opcionalno — ostavi prazno za automatski opis koji počinje nazivom artikla.',
    )
    h1_naslov = models.CharField(
        max_length=200, blank=True,
        verbose_name='H1 naslov',
        help_text='Opcionalno — prazno = naziv artikla.',
    )
    seo_tekst_iznad = models.TextField(
        blank=True,
        verbose_name='SEO tekst iznad proizvoda',
        help_text='Opcionalno. Tekst iznad detalja artikla.',
    )
    seo_tekst_ispod = models.TextField(
        blank=True,
        verbose_name='SEO tekst ispod proizvoda',
        help_text='Opcionalno. Tekst ispod detalja artikla (npr. ispod opisa / povezanih).',
    )
    # Denormalizovana polja za pretragu (NOT NULL u bazi — default '' da Odoo import ne padne)
    search_keywords = models.TextField(
        blank=True,
        default='',
        editable=False,
        verbose_name='Search keywords',
        help_text='Interni blob za pretragu (opcionalno).',
    )
    naziv_normalized = models.CharField(
        max_length=220, blank=True, default='', db_index=True, editable=False,
    )
    sifra_normalized = models.CharField(
        max_length=80, blank=True, default='', db_index=True, editable=False,
    )
    barkod_normalized = models.CharField(
        max_length=80, blank=True, default='', db_index=True, editable=False,
    )
    search_document = models.TextField(
        blank=True, default='', editable=False,
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
            from .utils.images import apply_image_processing, process_product_image_named
            image_base = (self.slug or '').strip() or self.naziv or 'artikal'
            apply_image_processing(
                self,
                'slika',
                post_process=process_product_image_named(image_base),
            )
        # NOT NULL search kolone — nikad ne šalji NULL (Odoo import / bulk create)
        if self.search_keywords is None:
            self.search_keywords = ''
        if self.naziv_normalized is None:
            self.naziv_normalized = ''
        if self.sifra_normalized is None:
            self.sifra_normalized = ''
        if self.barkod_normalized is None:
            self.barkod_normalized = ''
        if self.search_document is None:
            self.search_document = ''
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

    def _own_pakovanje_komada(self):
        """Pakovanje sa polja artikla (bez varijacija) — baza za fallback."""
        try:
            n = int(self.pakovanje_komada or 0)
        except (TypeError, ValueError):
            return 0
        return n if n > 1 else 0

    def _katalog_pakovanje_sizes(self):
        """
        Efektivne količine pakovanja za prikaz na pretrazi/katalogu.
        Varijacija: svoje pakovanje_komada, inače pakovanje artikla.
        Bez varijacija: samo pakovanje artikla.
        Vraća listu int (0 = po komadu, >1 = pakovanje).
        """
        variations = list(self.varijacije.all())
        if not variations:
            n = self._own_pakovanje_komada()
            return [n] if n > 1 else []

        product_n = self._own_pakovanje_komada()
        sizes = []
        for variation in variations:
            try:
                n = int(variation.pakovanje_komada or 0)
            except (TypeError, ValueError):
                n = 0
            if n > 1:
                sizes.append(n)
            elif product_n > 1:
                sizes.append(product_n)
            else:
                sizes.append(0)
        return sizes

    @property
    def je_pakovanje(self):
        """True ako se prodaje kao pakovanje (više komada u cijeni) — artikal ili varijacije."""
        return any(n > 1 for n in self._katalog_pakovanje_sizes())

    @property
    def pakovanje_komada_prikaz(self):
        """
        Broj komada u pakovanju sa polja artikla (0 ako nije).
        Ne gleda varijacije — ProductVariation.pakovanje_komada_prikaz pada na ovo.
        """
        return self._own_pakovanje_komada()

    @property
    def pakovanje_jedinstvena_kolicina(self):
        """
        Zajednička količina pakovanja na katalogu, ili 0 ako nema / nije ista.
        """
        sizes = self._katalog_pakovanje_sizes()
        pack_sizes = {n for n in sizes if n > 1}
        if len(pack_sizes) != 1:
            return 0
        # Ako postoje i varijacije „po komadu” uz pakovanja — nije jedinstveno
        if any(n <= 1 for n in sizes):
            return 0
        return pack_sizes.pop()

    @property
    def pakovanje_razlicite_kolicine(self):
        """True kad varijacije imaju različite (efektivne) količine pakovanja."""
        if not self.je_pakovanje:
            return False
        return self.pakovanje_jedinstvena_kolicina <= 1

    @property
    def pakovanje_label(self):
        """Kratka oznaka npr. „Pakovanje 9 kom.” ili „Pakovanje” ako su količine različite."""
        if not self.je_pakovanje:
            return ''
        n = self.pakovanje_jedinstvena_kolicina
        if n > 1:
            return f'Pakovanje {n} kom.'
        return 'Pakovanje'

    @property
    def pakovanje_cijena_hint(self):
        """
        Na pretrazi/katalogu:
        - iste količine u varijacijama → „Cijena za 10 kom.”
        - različite količine → „Cijena na pakovanje / ne na komad”
        - bez varijacija / jedno pakovanje → „Cijena za N kom.”
        """
        if not self.je_pakovanje:
            return ''
        n = self.pakovanje_jedinstvena_kolicina
        if n > 1:
            return f'Cijena za {n} kom.'
        return 'Cijena na pakovanje / ne na komad'

    @property
    def pakovanje_uz_cijenu(self):
        """
        Suffix pored cijene: „5 komada” ili „pakovanje”.
        Prikaz: 15.55 KM - 5 komada
        """
        if not self.je_pakovanje:
            return ''
        n = self.pakovanje_jedinstvena_kolicina
        if n > 1:
            return f'{n} komada'
        return 'pakovanje'

    @property
    def ima_varijacije(self):
        """True ako artikal ima bar jednu varijaciju (koristi prefetch ako postoji)."""
        try:
            cache = getattr(self, '_prefetched_objects_cache', None) or {}
            if 'varijacije' in cache:
                return len(cache['varijacije']) > 0
        except Exception:
            pass
        try:
            return self.varijacije.exists()
        except Exception:
            return False

    @property
    def pakovanje_uz_cijenu_kartica(self):
        """
        Na početnoj/katalogu: „- N komada” SAMO ako artikal NEMA varijacije.
        S varijacijama se pakovanje prikazuje tek na stranici artikla pored varijacija.
        """
        if self.ima_varijacije:
            return ''
        return self.pakovanje_uz_cijenu

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
    def akcija_postotak_prikaz(self):
        """−% za badge na product detail (samo ako je sam artikal na akciji)."""
        if not self.na_akciji:
            return None
        return _izracunaj_postotak_umanjenja(self.bazna_cijena, self.prikazna_cijena)

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

    def missing_storefront_fields(self):
        """Polja koja fale za uredan prikaz na sajtu (Edit mode)."""
        missing = []
        if not self.kategorija_id:
            missing.append('kategorija')
        try:
            if self.cijena is None or self.cijena <= 0:
                missing.append('cijena')
        except (TypeError, ArithmeticError):
            missing.append('cijena')
        if not self.ima_sliku:
            missing.append('slika')
        if not (self.opis or '').strip():
            missing.append('opis')
        return missing

    @property
    def prikazna_slika_responsive(self):
        from .utils.images import product_image_responsive_meta

        slika = self.prikazna_slika
        if not slika:
            return None
        return product_image_responsive_meta(slika)

    @property
    def seo_title(self):
        """
        <title> / og:title.
        Ručni meta_title ima prednost; inače: Naziv | Brend | shop (max ~60–70).
        """
        if self.meta_title:
            return self.meta_title.strip()
        from .utils.seo import auto_product_seo_title
        return auto_product_seo_title(self)

    @property
    def seo_description(self):
        """Meta description — ručni ili pametni default s benefitima + brand/kategorija."""
        if self.meta_description:
            return self.meta_description.strip()
        from .utils.seo import auto_product_seo_description
        return auto_product_seo_description(self)

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
            from .utils.images import apply_image_processing, process_product_image_named
            product = self.product
            base = (
                (getattr(product, 'slug', None) or '').strip()
                or getattr(product, 'naziv', None)
                or 'artikal'
            )
            # redoslijed/id možda još nisu finalni pri create — galerija-N rješava rename komanda
            label = f'{base}-galerija'
            apply_image_processing(
                self,
                'slika',
                post_process=process_product_image_named(label),
            )
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
    pakovanje_komada = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name='Pakovanje (komada)',
        help_text=(
            'Ako ova varijacija ima drugačije pakovanje od artikla — unesi broj komada. '
            'Prazno = koristi pakovanje sa artikla (ako postoji).'
        ),
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
    naziv_normalized = models.CharField(
        max_length=120, blank=True, default='', db_index=True, editable=False,
    )
    sifra_normalized = models.CharField(
        max_length=80, blank=True, default='', db_index=True, editable=False,
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
    def akcija_postotak_prikaz(self):
        """−% za badge (varijacija na akciji)."""
        if not self.na_akciji:
            return None
        return _izracunaj_postotak_umanjenja(self.bazna_cijena, self.prikazna_cijena)

    @property
    def pakovanje_komada_prikaz(self):
        """Komada u pakovanju: varijacija > artikal."""
        try:
            n = int(self.pakovanje_komada or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 1:
            return n
        art = getattr(self, 'artikal', None)
        if art is not None:
            return art.pakovanje_komada_prikaz
        return 0

    @property
    def je_pakovanje(self):
        return self.pakovanje_komada_prikaz > 1

    @property
    def pakovanje_label(self):
        n = self.pakovanje_komada_prikaz
        if n <= 1:
            return ''
        return f'Pakovanje {n} kom.'

    @property
    def pakovanje_cijena_hint(self):
        n = self.pakovanje_komada_prikaz
        if n <= 1:
            return ''
        return f'Cijena za {n} kom.'

    @property
    def pakovanje_uz_cijenu(self):
        """Suffix pored cijene: „5 komada”."""
        n = self.pakovanje_komada_prikaz
        if n <= 1:
            return ''
        return f'{n} komada'

    @property
    def status_dostupnosti(self):
        return 'Na stanju' if self.na_stanju else 'Rasprodato'

    @property
    def ima_sliku(self):
        return bool(self.slika)

    def save(self, *args, **kwargs):
        self.naziv_normalized = (self.naziv or '').casefold()[:120]
        self.sifra_normalized = (self.sifra or '').casefold()[:80]
        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            kwargs['update_fields'] = list(set(update_fields) | {'naziv_normalized', 'sifra_normalized'})
        if self.akcija_postotak:
            self.akcijska_cijena = _izracunaj_akcijsku_od_postotka(
                self.bazna_cijena, self.akcija_postotak,
            )
        if self.slika:
            from .utils.images import apply_image_processing, process_product_image_named
            product = self.artikal
            product_base = (
                (getattr(product, 'slug', None) or '').strip()
                or getattr(product, 'naziv', None)
                or 'artikal'
            )
            var_part = self.naziv or str(self.pk or 'var')
            label = f'{product_base}-{var_part}'
            apply_image_processing(
                self,
                'slika',
                post_process=process_product_image_named(label),
            )
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


class LoyaltyPurchase(models.Model):
    """
    Evidentirana (ručna / u prodavnici) kupovina na loyalty kartici.
    Online narudžbe se vode preko Order — ovo je istorija „Evidentiraj kupovinu”.
    """
    class Verifikacija(models.TextChoices):
        OTP = 'otp', 'Kod (Viber/WhatsApp)'
        ADMIN = 'admin', 'Admin override (bez koda)'

    class Placanje(models.TextChoices):
        GOTOVINA = 'gotovina', 'Gotovina'
        KARTICA = 'kartica', 'Kartica'

    kartica = models.ForeignKey(
        LoyaltyCard,
        on_delete=models.CASCADE,
        related_name='evidentirane_kupovine',
        verbose_name='Loyalty kartica',
    )
    iznos = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name='Iznos (KM)',
    )
    napomena = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Napomena',
    )
    verifikacija = models.CharField(
        max_length=10,
        choices=Verifikacija.choices,
        default=Verifikacija.OTP,
        verbose_name='Način potvrde',
        help_text='otp = 4-cifreni kod poslan kupcu; admin = bez koda (nema internet).',
    )
    kreirao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='loyalty_evidentirane_kupovine',
        verbose_name='Evidentirao',
    )
    kreirano = models.DateTimeField(auto_now_add=True, verbose_name='Datum')
    placanje = models.CharField(
        max_length=12,
        choices=Placanje.choices,
        default=Placanje.GOTOVINA,
        verbose_name='Način plaćanja',
    )

    class Meta:
        verbose_name = 'Evidentirana loyalty kupovina'
        verbose_name_plural = 'Evidentirane loyalty kupovine'
        ordering = ['-kreirano']

    def __str__(self):
        return f'{self.iznos} KM — {self.kartica_id}'


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
    telefon_verifikovan = models.BooleanField(
        default=False,
        verbose_name='Telefon verifikovan (Viber)',
        help_text='Potvrđeno da broj pripada kupcu (kod poslan preko Vibera).',
    )
    telefon_verifikovan_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Telefon verifikovan u',
    )
    adresa = models.CharField(max_length=300, blank=True)
    grad = models.CharField(max_length=100, blank=True)
    postanski_broj = models.CharField(max_length=20, blank=True)
    loyalty_napomena = models.TextField(blank=True, verbose_name='Loyalty napomena')

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
        REZERVACIJA = 'rezervacija', 'Rezervacija'
        POTVRDJENA = 'potvrdjena', 'Potvrđena'
        POSLANA = 'poslana', 'Poslana'
        ZAVRSENA = 'zavrsena', 'Validatovana'
        OTKAZANA = 'otkazana', 'Otkazana'

    class Izvor(models.TextChoices):
        WEBSHOP = 'webshop', 'Webshop'
        MAGACIN = 'magacin', 'Ručni unos (Magacin)'

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
    popust_detalji = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Detalji popusta na narudžbi',
        help_text='Lista {opis, iznos} za kupon, recovery, nagradu…',
    )
    ukupno = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOVA)
    class LagerStatus(models.TextChoices):
        NIJE = 'nije', '—'
        REZERVISANO = 'rezervisano', 'Rezervisano'
        VALIDIRANO = 'validirano', 'Validatovano'
        OTKAZANO = 'otkazano', 'Otkazano'

    izvor = models.CharField(
        max_length=20,
        choices=Izvor.choices,
        default=Izvor.WEBSHOP,
        db_index=True,
        verbose_name='Izvor',
    )
    lager_status = models.CharField(
        max_length=20,
        choices=LagerStatus.choices,
        default=LagerStatus.NIJE,
        db_index=True,
        verbose_name='Magacin lager',
    )
    kreirana = models.DateTimeField(auto_now_add=True)
    odstampana = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='Odštampana',
        help_text='Staff je odštampao narudžbu (račun + garancija + packing).',
    )
    odstampana_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Odštampana u',
    )
    zapakovana = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='Zapakovana',
        help_text='Validirana narudžba je automatski skinuta s pakovanja.',
    )
    zapakovana_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Zapakovana u',
    )
    packing_odstampana = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='Packing odštampan',
        help_text='Odštampan packing list (lokacije po kupcu). Skida se s liste narudžbi.',
    )
    packing_odstampana_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Packing odštampan u',
    )
    stanje_skinuto = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='Stanje skinuto',
        help_text='Zaliha skinuta iz Odoa (zapakovano i poslato — Brza pošta).',
    )
    stanje_skinuto_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Stanje skinuto u',
    )
    brza_posta_unijeta = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='Unijeto u Brzu poštu',
    )
    brza_posta_unijeta_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Unijeto u Brzu poštu u',
    )
    pick_state = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Picking stanje',
    )
    pick_claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='preuzete_narudzbe',
        verbose_name='Picking preuzeo',
    )
    pick_claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Picking preuzeto',
    )
    pick_claimed_name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name='Picking preuzeo (ime)',
    )
    odoo_sale_order_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name='Odoo sale.order ID',
        help_text='ID Sales narudžbe kreirane u Odoo iz web narudžbe.',
    )
    odoo_sale_order_name = models.CharField(
        max_length=40,
        blank=True,
        verbose_name='Odoo SO broj',
        help_text='Npr. S00042 — broj u Odoo Sales.',
    )
    odoo_sale_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Odoo SO kreiran u',
    )
    xexpress_sifra = models.CharField(
        max_length=40,
        blank=True,
        db_index=True,
        verbose_name='X-Express šifra',
        help_text='Šifra pošiljke koju vrati X-Express API.',
    )
    xexpress_poslano_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='X-Express poslano u',
    )

    class Meta:
        verbose_name = 'Narudžba'
        verbose_name_plural = 'Narudžbe'
        ordering = ['-kreirana']

    def save(self, *args, **kwargs):
        if not self.broj:
            from django.db.models.functions import Length
            last = (
                Order.objects.annotate(_len=Length('broj'))
                .filter(_len=4)
                .order_by('-broj')
                .values_list('broj', flat=True)
                .first()
            )
            seq = int(last) + 1 if last and str(last).isdigit() else 1
            self.broj = f'{seq:05d}' if seq > 9999 else f'{seq:04d}'
        super().save(*args, **kwargs)

    @property
    def pdv_pregled(self):
        from .cart import izracunaj_pdv
        return izracunaj_pdv(self.ukupno)

    @property
    def dostava_naziv(self):
        return SiteSettings.load().dostava_naziv

    def get_status_label(self):
        if self.lager_status == self.LagerStatus.VALIDIRANO:
            return 'Validatovana'
        return self.Status(self.status).label

    def get_status_pill_class(self):
        if (
            self.lager_status == self.LagerStatus.VALIDIRANO
            or self.status == self.Status.ZAVRSENA
        ):
            return 'mg-st-zavrsena'
        return f'mg-st-{self.status}'

    def placeno_karticom(self):
        for row in (self.popust_detalji or []):
            if not isinstance(row, dict):
                continue
            if row.get('placanje') == 'kartica':
                return True
            opis = str(row.get('opis') or '').casefold()
            if 'plaćeno karticom' in opis or 'placeno karticom' in opis:
                return True
        note = (self.napomena or '').casefold()
        return 'plaćeno karticom' in note or 'placeno karticom' in note

    def packing_placanje_label(self):
        return 'KARTICA' if self.placeno_karticom() else 'GOTOVINSKI'

    @property
    def barkod(self):
        """Code128 vrijednost za picking skener — jedinstvena po broju naloga."""
        return f'OZB{self.broj}'

    @property
    def pick_shortages(self):
        rows = []
        for item in self.stavke.all():
            got = item.kolicina_pokupljeno
            if got is None or got >= item.kolicina:
                continue
            rows.append({
                'naziv': item.product_naziv or item.naziv,
                'got': got,
                'need': item.kolicina,
            })
        return rows

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
    bazna_cijena = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Regularna cijena (jed.)',
        help_text='Cijena prije sniženja na ovoj stavci (ako postoji popust).',
    )
    popust_opis = models.CharField(
        max_length=300,
        blank=True,
        verbose_name='Izvor popusta',
        help_text='Npr. AI dwell −10%, Akcija Timer, Live ponuda, Bundle…',
    )
    popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust (%)',
    )
    popust_iznos = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Ušteda ukupno (KM)',
        help_text='Ukupna ušteda na stavci (sve komade) u odnosu na regularnu cijenu.',
    )
    kolicina = models.PositiveIntegerField(default=1)
    kolicina_pokupljeno = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Pokupljeno',
        help_text='Količina potvrđena na pickingu. Ako je manja, faktura ide po ovoj količini.',
    )
    rezervni_dio = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name='Rezervni dio',
    )

    class Meta:
        verbose_name = 'Stavka narudžbe'
        verbose_name_plural = 'Stavke narudžbe'

    @property
    def kolicina_faktura(self):
        if self.kolicina_pokupljeno is not None:
            return self.kolicina_pokupljeno
        return self.kolicina

    @property
    def ukupno(self):
        return self.cijena * self.kolicina_faktura

    @property
    def kolicina_range(self):
        return range(self.kolicina)

    @property
    def puni_naziv(self):
        if self.varijacija_naziv:
            return f'{self.product_naziv or self.naziv} — {self.varijacija_naziv}'
        return self.product_naziv or self.naziv

    @property
    def ima_snizenje(self):
        if self.popust_opis:
            return True
        if self.bazna_cijena is not None and self.bazna_cijena > self.cijena:
            return True
        return bool(self.popust_iznos and self.popust_iznos > 0)

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
    body = models.TextField(max_length=2000, blank=True)
    # Ponuda artikla u chatu (staff → kupac)
    product = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chat_messages',
        verbose_name='Artikal (ponuda)',
    )
    product_popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust na artikal (%)',
        help_text='Opcionalno. Prazno ili 0 = regularna cijena.',
    )
    product_cijena = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Cijena ponude (KM)',
    )
    product_bazna_cijena = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Regularna cijena (KM)',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    read_by_staff = models.BooleanField(default=False)
    read_by_customer = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Chat poruka'
        verbose_name_plural = 'Chat poruke'
        ordering = ['created_at']

    @property
    def is_product_offer(self):
        return bool(self.product_id)

    def __str__(self):
        if self.product_id:
            return f'{self.get_sender_type_display()}: artikal #{self.product_id}'
        return f'{self.get_sender_type_display()}: {(self.body or "")[:40]}'


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
        max_length=32,
        blank=True,
        db_index=True,
        verbose_name='Izvor dolaska',
        help_text=(
            'direct | google | google_ads | facebook | facebook_ads | '
            'instagram | instagram_ads | other'
        ),
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
    savjetnik = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Ribolovački savjetnik',
        help_text=(
            'Live stanje savjetnika: answers, step, offer_shown, offer_accepted, set_names…'
        ),
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
        OFFER = 'offer', 'Prihvaćena ponuda'
        ADVISOR = 'advisor', 'Ribolovački savjetnik'

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


class Uvoz(models.Model):
    """Spremljeni uvoz iz Excel uvoznice (pregled / izmjena / brisanje)."""

    class Izvor(models.TextChoices):
        SAJT = 'sajt', 'Sajt'
        MAGACIN = 'magacin', 'Magacin'

    naziv = models.CharField(max_length=200, verbose_name='Naziv uvoza')
    izvor = models.CharField(
        max_length=20,
        choices=Izvor.choices,
        default=Izvor.SAJT,
        db_index=True,
        verbose_name='Izvor',
    )
    fajl_naziv = models.CharField(max_length=255, blank=True, verbose_name='Ime fajla')
    napomena = models.TextField(blank=True, verbose_name='Napomena')
    kreirao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uvozi',
        verbose_name='Uvezao',
    )
    kreiran = models.DateTimeField(auto_now_add=True, verbose_name='Kreiran')
    azuriran = models.DateTimeField(auto_now=True, verbose_name='Ažuriran')
    broj_redova = models.PositiveIntegerField(default=0, verbose_name='Broj redova')
    broj_azurirano = models.PositiveIntegerField(default=0, verbose_name='Ažurirano artikala')
    broj_kreirano = models.PositiveIntegerField(default=0, verbose_name='Kreirano artikala')
    broj_preskoceno = models.PositiveIntegerField(default=0, verbose_name='Preskočeno')
    broj_mpc_promjena = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Promjena MPC',
    )
    ukupna_fakturna = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name='Ukupna fakturna',
    )
    log_detalji = models.JSONField(default=list, blank=True, verbose_name='Log')

    class Meta:
        verbose_name = 'Uvoz'
        verbose_name_plural = 'Uvozi'
        ordering = ['-kreiran']

    def __str__(self):
        return self.naziv or f'Uvoz #{self.pk}'

    @property
    def stavke_count(self):
        return self.stavke.count()


class UvozStavka(models.Model):
    """Jedan red uvoza (kao u Excelu)."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Na čekanju'
        CREATED = 'created', 'Kreirano'
        UPDATED = 'updated', 'Ažurirano'
        SKIPPED = 'skipped', 'Preskočeno'
        ERROR = 'error', 'Greška'

    uvoz = models.ForeignKey(
        Uvoz,
        on_delete=models.CASCADE,
        related_name='stavke',
        verbose_name='Uvoz',
    )
    artikal_naziv = models.CharField(max_length=200, verbose_name='Artikal')
    kolicina = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, verbose_name='Količina',
    )
    fakturna = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True, verbose_name='Fakturna',
    )
    nabavna = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True, verbose_name='Nabavna',
    )
    vpc_netto = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True, verbose_name='Vpc netto',
    )
    mpc_brutto = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Mpc brutto',
    )
    vpc_marza = models.DecimalField(
        max_digits=12, decimal_places=6, null=True, blank=True, verbose_name='Vpc marža',
    )
    ukupno_fakturna = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, verbose_name='Ukupno Fakturna',
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uvoz_stavke',
        verbose_name='Artikal na sajtu',
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name='Status',
    )
    poruka = models.CharField(max_length=300, blank=True, verbose_name='Poruka')
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Red')

    class Meta:
        verbose_name = 'Stavka uvoza'
        verbose_name_plural = 'Stavke uvoza'
        ordering = ['redoslijed', 'id']

    def __str__(self):
        return f'{self.artikal_naziv} ({self.get_status_display()})'

    @property
    def vpc_marza_pct(self):
        """
        Vpc marža kao postotak za prikaz.
        Excel često šalje udeo (0.69 → 69%), ponekad već %.
        """
        if self.vpc_marza is None:
            return None
        try:
            val = Decimal(self.vpc_marza)
        except Exception:
            return None
        # udeo 0–2 → pretvori u %; inače tretiraj kao već unesen %
        if abs(val) <= 2:
            val = val * Decimal('100')
        return val.quantize(Decimal('0.01'))

    @property
    def vpc_marza_pct_display(self):
        pct = self.vpc_marza_pct
        if pct is None:
            return ''
        return f'{pct}%'


class AdvisorBeginnerFishType(models.Model):
    """
    Tip seta za savjetnik (Saranski set, Feeder set, Pečaljke za plovak…).
    Varaličarski podtipovi: stuka (lov štuke), som (lov soma), ul (UL ribolov)
    — u chatu se grupišu pod „Varaličarski set”.
    """
    code = models.SlugField(
        max_length=40,
        unique=True,
        verbose_name='Kod',
        help_text=(
            'Interni kod, npr. saranski, feeder, plovak. '
            'Za varaličarski: stuka, som, ul (grupišu se u chatu).'
        ),
    )
    naziv = models.CharField(max_length=100, verbose_name='Naziv')
    emoji = models.CharField(max_length=8, blank=True, default='', verbose_name='Emoji')
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Savjetnik — tip seta'
        verbose_name_plural = 'Savjetnik — tipovi setova'
        ordering = ['redoslijed', 'naziv']

    def __str__(self):
        return f'{self.emoji} {self.naziv}'.strip() if self.emoji else self.naziv

    @property
    def setovi_count(self):
        return self.setovi.filter(aktivan=True).count()


class AdvisorBeginnerSet(models.Model):
    """
    Jedan set/komplet unutar tipa seta u savjetniku.
    Možeš dodati koliko god setova želiš (osnovni, srednji…).
    """
    fish_type = models.ForeignKey(
        AdvisorBeginnerFishType,
        on_delete=models.CASCADE,
        related_name='setovi',
        verbose_name='Tip seta',
    )
    naziv = models.CharField(
        max_length=120,
        verbose_name='Naziv seta',
        help_text='Npr. Osnovni komplet, Srednji, Napredni…',
    )
    emoji = models.CharField(max_length=8, blank=True, default='', verbose_name='Emoji')
    popis = models.TextField(
        blank=True,
        verbose_name='Kratki opis (opcionalno)',
        help_text='Samo za admin / internu napomenu. Na sajtu se ne mora prikazati.',
    )
    popust_postotak = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name='Popust na set (%)',
        help_text='Opcionalno. Npr. 10 = −10% na cijeli set. Prazno = bez popusta.',
    )
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')
    aktivan = models.BooleanField(default=True, verbose_name='Aktivan')

    class Meta:
        verbose_name = 'Početnik — set'
        verbose_name_plural = 'Početnik — setovi'
        ordering = ['fish_type__redoslijed', 'redoslijed', 'id']

    def __str__(self):
        return f'{self.fish_type.naziv}: {self.naziv}'

    def regularni_iznos(self):
        total = Decimal('0')
        for item in self.stavke.select_related('product'):
            try:
                price = Decimal(str(item.product.prikazna_cijena))
            except Exception:
                price = Decimal('0')
            total += price * Decimal(item.kolicina or 1)
        return total.quantize(Decimal('0.01'))

    def snizeni_iznos(self):
        total = self.regularni_iznos()
        pct = self.popust_postotak
        if pct is None or pct <= 0:
            return total
        if pct > 100:
            pct = Decimal('100')
        faktor = Decimal('1') - (Decimal(pct) / Decimal('100'))
        return (total * faktor).quantize(Decimal('0.01'))

    def ima_popust(self):
        return bool(self.popust_postotak and self.popust_postotak > 0)


class AdvisorBeginnerSetItem(models.Model):
    """Artikal u početničkom setu (samo na stanju)."""
    set = models.ForeignKey(
        AdvisorBeginnerSet,
        on_delete=models.CASCADE,
        related_name='stavke',
        verbose_name='Set',
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        related_name='advisor_beginner_set_items',
        verbose_name='Artikal',
        limit_choices_to={'aktivan': True, 'na_stanju': True},
    )
    kolicina = models.PositiveSmallIntegerField(default=1, verbose_name='Količina')
    redoslijed = models.PositiveIntegerField(default=0, verbose_name='Redoslijed')

    class Meta:
        verbose_name = 'Stavka seta'
        verbose_name_plural = 'Stavke seta'
        ordering = ['redoslijed', 'id']
        unique_together = [('set', 'product')]

    def __str__(self):
        return f'{self.product} ×{self.kolicina}'

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.product_id:
            p = self.product
            if not getattr(p, 'aktivan', True):
                raise ValidationError({'product': 'Artikal mora biti aktivan.'})
            if not getattr(p, 'na_stanju', False):
                raise ValidationError({
                    'product': 'Možeš dodati samo artikle koji su na stanju.',
                })

    def linija_iznos(self):
        try:
            price = Decimal(str(self.product.prikazna_cijena))
        except Exception:
            price = Decimal('0')
        return (price * Decimal(self.kolicina or 1)).quantize(Decimal('0.01'))


class WarehouseLocation(models.Model):
    sifra = models.CharField(max_length=20, unique=True, verbose_name='Šifra')
    naziv = models.CharField(max_length=120, verbose_name='Naziv')
    opis = models.CharField(max_length=300, blank=True, verbose_name='Opis')
    aktivan = models.BooleanField(default=True, db_index=True)
    redoslijed = models.PositiveIntegerField(default=0)
    odoo_location_id = models.PositiveIntegerField(
        blank=True, null=True, unique=True, verbose_name='Odoo location ID',
    )
    odoo_location_path = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = 'Magacin lokacija'
        verbose_name_plural = 'Magacin lokacije'
        ordering = ['redoslijed', 'sifra', 'id']

    def __str__(self):
        return f'{self.sifra} ({self.naziv})' if self.naziv else self.sifra

    @property
    def label(self):
        if self.naziv:
            return f'{self.sifra} ({self.naziv})'
        return self.sifra


class MagacinPopis(models.Model):
    class Status(models.TextChoices):
        U_TOKU = 'u_toku', 'U toku'
        PAUZIRAN = 'pauziran', 'Pauziran'
        ZAVRSEN = 'zavrsen', 'Završen'

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.U_TOKU, db_index=True)
    location = models.ForeignKey(
        'WarehouseLocation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='popisi',
    )
    kreiran = models.DateTimeField(auto_now_add=True)
    azuriran = models.DateTimeField(auto_now=True)
    zavrsen_at = models.DateTimeField(null=True, blank=True)
    odstampan = models.BooleanField(default=False)
    kreirao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_popisi',
    )

    class Meta:
        verbose_name = 'Magacin popis'
        verbose_name_plural = 'Magacin popisi'
        ordering = ['-kreiran']

    def __str__(self):
        return f'Popis #{self.pk} ({self.get_status_display()})'


class MagacinPopisStavka(models.Model):
    popis = models.ForeignKey(MagacinPopis, on_delete=models.CASCADE, related_name='stavke')
    product = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='popis_stavke',
    )
    variation = models.ForeignKey(
        'ProductVariation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='popis_stavke',
    )
    naziv = models.CharField(max_length=200)
    sifra = models.CharField(max_length=SIFRA_MAX_LENGTH, blank=True)
    ocekivano = models.PositiveIntegerField(default=0)
    kolicina = models.PositiveIntegerField(default=1)
    cekirano = models.BooleanField(default=False)
    redoslijed = models.PositiveIntegerField(default=0)
    kreiran = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Popis stavka'
        verbose_name_plural = 'Popis stavke'
        ordering = ['redoslijed', 'id']

    def __str__(self):
        return f'{self.naziv} × {self.kolicina}'


class MagacinVpNarudzba(models.Model):
    class Status(models.TextChoices):
        U_TOKU = 'u_toku', 'U toku'
        ZAVRSENA = 'zavrsena', 'Završena'

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.U_TOKU, db_index=True)
    customer = models.ForeignKey(
        'WarehouseCustomer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vp_narudzbe',
    )
    ime_prezime = models.CharField(max_length=200, blank=True)
    telefon = models.CharField(max_length=30, blank=True)
    adresa = models.CharField(max_length=300, blank=True)
    grad = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    postanski_broj = models.CharField(max_length=20, blank=True)
    order = models.ForeignKey(
        'Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vp_nacrti',
    )
    kreiran = models.DateTimeField(auto_now_add=True)
    azuriran = models.DateTimeField(auto_now=True)
    zavrsen_at = models.DateTimeField(null=True, blank=True)
    kreirao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_vp_narudzbe',
    )
    bulk = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Magacin VP narudžba'
        verbose_name_plural = 'Magacin VP narudžbe'
        ordering = ['-kreiran']

    def __str__(self):
        who = self.ime_prezime or 'bez kupca'
        return f'VP #{self.pk} ({who})'


class MagacinVpStavka(models.Model):
    narudzba = models.ForeignKey(MagacinVpNarudzba, on_delete=models.CASCADE, related_name='stavke')
    product = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vp_stavke',
    )
    variation = models.ForeignKey(
        'ProductVariation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='vp_stavke',
    )
    naziv = models.CharField(max_length=200)
    sifra = models.CharField(max_length=SIFRA_MAX_LENGTH, blank=True)
    kolicina = models.PositiveIntegerField(default=1)
    cijena = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    mpc = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    mp_ok = models.BooleanField(default=False)
    redoslijed = models.PositiveIntegerField(default=0)
    kreiran = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'VP stavka'
        verbose_name_plural = 'VP stavke'
        ordering = ['redoslijed', 'id']

    @property
    def ukupno(self):
        return (self.cijena or Decimal('0.00')) * self.kolicina

    def __str__(self):
        return f'{self.naziv} × {self.kolicina}'


class WarehouseSupplier(models.Model):
    naziv = models.CharField(max_length=160, unique=True)
    aktivan = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Dobavljač'
        verbose_name_plural = 'Dobavljači'
        ordering = ['naziv']

    def __str__(self):
        return self.naziv


class MagacinPonuda(models.Model):
    class Status(models.TextChoices):
        NACRT = 'nacrt', 'Nacrt'
        OBJAVLJENA = 'objavljena', 'Objavljena'
        PRIHVACENA = 'prihvacena', 'Prihvaćena'

    broj = models.CharField(max_length=20, unique=True, editable=False)
    token = models.CharField(max_length=40, unique=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NACRT, db_index=True,
    )
    ime_prezime = models.CharField(max_length=200, blank=True, verbose_name='Kupac')
    telefon = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    adresa = models.CharField(max_length=300, blank=True)
    grad = models.CharField(max_length=100, blank=True)
    napomena = models.TextField(blank=True)
    popust_postotak = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        verbose_name='Popust (%)',
    )
    popust_iznos = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name='Popust (KM)',
    )
    kreiran = models.DateTimeField(auto_now_add=True)
    azuriran = models.DateTimeField(auto_now=True)
    objavljena_at = models.DateTimeField(null=True, blank=True)
    prihvacena_at = models.DateTimeField(null=True, blank=True)
    order = models.OneToOneField(
        'Order',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_ponuda',
    )
    kreirao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_ponude',
    )

    class Meta:
        verbose_name = 'Magacin ponuda'
        verbose_name_plural = 'Magacin ponude'
        ordering = ['-kreiran']

    def __str__(self):
        return f'Ponuda {self.broj}'

    def save(self, *args, **kwargs):
        import secrets

        if not self.token:
            self.token = secrets.token_urlsafe(18)[:32]
        if not self.broj:
            last = MagacinPonuda.objects.order_by('-id').values_list('id', flat=True).first() or 0
            seq = int(last) + 1
            self.broj = f'P-{seq:04d}'
        super().save(*args, **kwargs)


class MagacinPonudaStavka(models.Model):
    ponuda = models.ForeignKey(MagacinPonuda, on_delete=models.CASCADE, related_name='stavke')
    product = models.ForeignKey(
        'Product',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ponuda_stavke',
    )
    variation = models.ForeignKey(
        'ProductVariation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ponuda_stavke',
    )
    naziv = models.CharField(max_length=200)
    sifra = models.CharField(max_length=SIFRA_MAX_LENGTH, blank=True)
    kolicina = models.PositiveIntegerField(default=1)
    cijena = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'),
        verbose_name='Cijena sa PDV',
    )
    manuelno = models.BooleanField(default=False)
    redoslijed = models.PositiveIntegerField(default=0)
    kreiran = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Stavka ponude'
        verbose_name_plural = 'Stavke ponude'
        ordering = ['redoslijed', 'id']

    @property
    def ukupno(self):
        return (self.cijena or Decimal('0.00')) * self.kolicina

    def __str__(self):
        return f'{self.naziv} × {self.kolicina}'


class MagacinMpDnevnoSkidanje(models.Model):
    kreiran = models.DateTimeField(auto_now_add=True, db_index=True)
    datum = models.DateField(null=True, blank=True, db_index=True)
    kreirao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mp_dnevna_skidanja',
    )
    fajl = models.FileField(upload_to='magacin/mp_dnevno/', blank=True)
    fajl_naziv = models.CharField(max_length=200, blank=True)
    raw_text = models.TextField(blank=True)
    skinuto_stavki = models.PositiveIntegerField(default=0)
    skinuto_komada = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Dnevno skidanje MP lagera'
        verbose_name_plural = 'Dnevna skidanja MP lagera'
        ordering = ['-datum', '-kreiran', '-id']

    def __str__(self):
        if self.datum:
            return f'MP skidanje {self.datum:%d.%m.%Y}'
        return f'MP skidanje {self.kreiran:%d.%m.%Y %H:%M}'


class MagacinMpDnevnoStavka(models.Model):
    skidanje = models.ForeignKey(
        MagacinMpDnevnoSkidanje, on_delete=models.CASCADE, related_name='stavke',
    )
    product = models.ForeignKey(
        'Product', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='mp_dnevno_stavke',
    )
    variation = models.ForeignKey(
        'ProductVariation', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='mp_dnevno_stavke',
    )
    sifra = models.CharField(max_length=SIFRA_MAX_LENGTH)
    naziv = models.CharField(max_length=200, blank=True)
    trazeno = models.PositiveIntegerField(default=0)
    kolicina = models.PositiveIntegerField(default=0)
    lokacija = models.CharField(max_length=300, blank=True)
    kreiran = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Stavka dnevnog MP skidanja'
        verbose_name_plural = 'Stavke dnevnog MP skidanja'
        ordering = ['id']

    def __str__(self):
        return f'{self.sifra} × {self.kolicina}'


class ProductWarehouseMeta(models.Model):
    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name='magacin_meta',
    )
    dobavljac = models.ForeignKey(
        WarehouseSupplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='artikli',
        verbose_name='Dobavljač',
    )
    tezina = models.CharField(max_length=40, blank=True, verbose_name='Težina')
    jedinica_mjere = models.CharField(
        max_length=20, default='kom', verbose_name='Jedinica mjere',
    )
    min_zaliha = models.PositiveIntegerField(default=0, verbose_name='Min. zaliha')
    veleprodajna_cijena = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name='Veleprodajna cijena',
    )
    mp_bez_lokacije = models.BooleanField(
        default=False,
        verbose_name='MP bez lokacije',
        help_text='Artikal je fizički u maloprodaji i ostaje na sajtu i kad nema magacinske lokacije.',
    )

    class Meta:
        verbose_name = 'Magacin meta artikla'
        verbose_name_plural = 'Magacin meta artikala'

    def __str__(self):
        return f'Magacin meta — {self.product}'


class WarehouseStock(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='magacin_zalihe',
    )
    variation = models.ForeignKey(
        ProductVariation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_zalihe',
    )
    variation_key = models.PositiveIntegerField(default=0, editable=False)
    location = models.ForeignKey(
        WarehouseLocation, on_delete=models.CASCADE, related_name='zalihe',
    )
    kolicina = models.IntegerField(default=0, verbose_name='Na stanju')
    rezervisano = models.IntegerField(default=0, verbose_name='Rezervisano')
    azurirano = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Zaliha po lokaciji'
        verbose_name_plural = 'Zalihe po lokacijama'
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'variation_key', 'location'],
                name='uniq_wh_stock_product_varkey_loc',
            ),
        ]
        indexes = [
            models.Index(fields=['product', 'location']),
        ]

    def save(self, *args, **kwargs):
        self.variation_key = int(self.variation_id or 0)
        super().save(*args, **kwargs)

    def __str__(self):
        target = self.variation or self.product
        return f'{target} @ {self.location.sifra}: {self.kolicina}'

    @property
    def dostupno(self):
        return max(0, int(self.kolicina or 0) - max(0, int(self.rezervisano or 0)))


class WarehouseMovement(models.Model):
    class Tip(models.TextChoices):
        PRIJEM = 'prijem', 'Prijem'
        PRODAJA = 'prodaja', 'Prodaja'
        TRANSFER = 'transfer', 'Transfer'
        KOREKCIJA = 'korekcija', 'Korekcija'
        REZERVACIJA = 'rezervacija', 'Rezervacija'
        SYNC = 'sync', 'Sinhronizacija'

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='magacin_kretanja',
    )
    variation = models.ForeignKey(
        ProductVariation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_kretanja',
    )
    location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.PROTECT,
        related_name='kretanja',
        verbose_name='Lokacija',
    )
    to_location = models.ForeignKey(
        WarehouseLocation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='kretanja_ulaz',
        verbose_name='Odredište',
    )
    tip = models.CharField(max_length=20, choices=Tip.choices, db_index=True)
    kolicina = models.IntegerField(verbose_name='Količina')
    napomena = models.CharField(max_length=300, blank=True)
    kreiran = models.DateTimeField(auto_now_add=True, db_index=True)
    korisnik = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_kretanja',
    )

    class Meta:
        verbose_name = 'Kretanje zalihe'
        verbose_name_plural = 'Kretanja zalihe'
        ordering = ['-kreiran', '-id']

    def __str__(self):
        return f'{self.get_tip_display()} {self.kolicina} — {self.product}'


class OrderStockHold(models.Model):
    class Status(models.TextChoices):
        REZERVISANO = 'rezervisano', 'Rezervisano'
        VALIDIRANO = 'validirano', 'Validatovano'
        OTKAZANO = 'otkazano', 'Otkazano'

    narudzba = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='magacin_holds')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='magacin_holds')
    variation = models.ForeignKey(
        ProductVariation, on_delete=models.SET_NULL, null=True, blank=True, related_name='magacin_holds',
    )
    location = models.ForeignKey(WarehouseLocation, on_delete=models.PROTECT, related_name='magacin_holds')
    kolicina = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REZERVISANO, db_index=True)
    kreiran = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Rezervacija narudžbe'
        verbose_name_plural = 'Rezervacije narudžbi'

    def __str__(self):
        return f'#{self.narudzba.broj} {self.kolicina} @ {self.location.sifra}'


class WarehouseCustomer(models.Model):
    ime_prezime = models.CharField(max_length=200)
    telefon = models.CharField(max_length=30)
    adresa = models.CharField(max_length=300, blank=True)
    grad = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    postanski_broj = models.CharField(max_length=20, blank=True)
    kreiran = models.DateTimeField(auto_now_add=True)
    azuriran = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Magacin kupac'
        verbose_name_plural = 'Magacin kupci'
        ordering = ['ime_prezime', 'id']
        indexes = [
            models.Index(fields=['ime_prezime']),
            models.Index(fields=['telefon']),
        ]

    def __str__(self):
        return f'{self.ime_prezime} {self.telefon}'.strip()


class MagacinDeklaracijaBrend(models.Model):
    """Brend s fiksnim poljima deklaracije za štampu naljepnica 65/A4."""

    POLJA = (
        ('naziv', 'Naziv'),
        ('uvoznik', 'Uvoznik'),
        ('adresa', 'Adresa'),
        ('zemlja_izvoza', 'Zemlja izvoza'),
        ('zemlja_porijekla', 'Zemlja porijekla'),
        ('godina_uvoza', 'Godina uvoza'),
        ('telefon', 'Telefon'),
    )

    naziv = models.CharField(max_length=120, unique=True, verbose_name='Naziv')
    uvoznik = models.CharField(max_length=200, blank=True, verbose_name='Uvoznik')
    adresa = models.CharField(max_length=300, blank=True, verbose_name='Adresa')
    zemlja_izvoza = models.CharField(max_length=80, blank=True, verbose_name='Zemlja izvoza')
    zemlja_porijekla = models.CharField(max_length=80, blank=True, verbose_name='Zemlja porijekla')
    godina_uvoza = models.CharField(max_length=20, blank=True, verbose_name='Godina uvoza')
    telefon = models.CharField(max_length=40, blank=True, verbose_name='Telefon')
    kreiran = models.DateTimeField(auto_now_add=True)
    azuriran = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Deklaracija brend'
        verbose_name_plural = 'Deklaracije brendovi'
        ordering = ['naziv', 'id']

    def deklaracija_redovi(self):
        rows = []
        for attr, label in self.POLJA:
            rows.append({
                'attr': attr,
                'label': label,
                'value': (getattr(self, attr) or '').strip(),
            })
        return rows

    def deklaracija_text(self):
        return '\n'.join(
            f'{row["label"]}: {row["value"]}'.rstrip()
            for row in self.deklaracija_redovi()
        )

    def __str__(self):
        return self.naziv


class NivelacijaOznaka(models.Model):
    """Artikal označen kao izmjenjen za konkretan uvoz (zadnju promjenu cijene)."""

    kljuc = models.CharField(max_length=220, db_index=True)
    uvoz = models.ForeignKey(
        Uvoz,
        on_delete=models.CASCADE,
        related_name='nivelacija_oznake',
    )
    product = models.ForeignKey(
        'Product',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='nivelacija_oznake',
    )
    kreiran = models.DateTimeField(auto_now_add=True)
    kreirao = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='nivelacija_oznake',
    )

    class Meta:
        verbose_name = 'Nivelacija oznaka'
        verbose_name_plural = 'Nivelacija oznake'
        constraints = [
            models.UniqueConstraint(fields=['kljuc', 'uvoz'], name='uniq_nivelacija_kljuc_uvoz'),
        ]

    def __str__(self):
        return f'{self.kljuc} @ {self.uvoz_id}'


class WarehouseSyncLog(models.Model):
    class Status(models.TextChoices):
        USPJEH = 'uspjeh', 'Uspješna'
        GRESKA = 'greska', 'Neuspješna'
        U_TOKU = 'u_toku', 'U toku'
        PREKINUT = 'prekinut', 'Prekinuta'

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.U_TOKU)
    izvor = models.CharField(max_length=80, default='Lokalna baza (SQLite)')
    poruka = models.CharField(max_length=400, blank=True)
    artikala = models.PositiveIntegerField(default=0)
    lokacija = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    trajanje_sekundi = models.PositiveIntegerField(default=0)
    job_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Sync job',
        help_text='Stanje chunkova da sync preživi reload i Render timeout.',
    )
    korisnik = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='magacin_syncovi',
    )

    class Meta:
        verbose_name = 'Magacin sync'
        verbose_name_plural = 'Magacin syncovi'
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.get_status_display()} {self.started_at}'

    @property
    def trajanje_label(self):
        total = int(self.trajanje_sekundi or 0)
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
        return f'{minutes:02d}:{seconds:02d}'
