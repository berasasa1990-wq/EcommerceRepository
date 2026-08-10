from django.db import migrations, models


def _clip(value, max_len):
    """PostgreSQL varchar(N) odbija duže stringove — uvijek skratiti pri seedu."""
    value = (value or '').strip()
    if len(value) <= max_len:
        return value
    cut = value[: max_len - 1]
    if ' ' in cut:
        cut = cut.rsplit(' ', 1)[0]
    return cut.rstrip(' ,;:-') + '…'


def seed_seo_defaults(apps, schema_editor):
    PageSEO = apps.get_model('EcommerceApp', 'PageSEO')
    SiteSettings = apps.get_model('EcommerceApp', 'SiteSettings')

    # title ≤70, meta_description ≤160 (CharField limits)
    defaults = {
        'home': {
            'seo_title': 'Oprema za ribolov | Online shop BiH — opremazaribolov.ba',
            'meta_description': (
                'Online shop opreme za ribolov u BiH: štapovi, mašinice, varalice, najloni i pribor. '
                'Brza dostava, akcije i podrška — opremazaribolov.ba.'
            ),
            'h1_naslov': 'Oprema za ribolov — online shop',
            'seo_tekst_ispod': (
                'opremazaribolov.ba je online trgovina ribolovačke opreme za bosanskohercegovačke '
                'ribare. U ponudi su štapovi, mašinice, varalice, najloni, hranilice i pribor '
                'provjerenih brendova. Naručite online — brza dostava širom BiH i savjeti pri kupovini.'
            ),
        },
        'akcija': {
            'seo_title': 'Akcija opreme za ribolov | Snižene cijene — opremazaribolov.ba',
            'meta_description': (
                'Akcijska ponuda ribolovačke opreme: snižene cijene na štapove, mašinice, varalice '
                'i pribor. Iskoristite popuste i brzu dostavu u BiH — opremazaribolov.ba.'
            ),
            'h1_naslov': 'Akcija — snižena oprema za ribolov',
            'seo_tekst_iznad': (
                'Pogledajte aktuelne akcije i snižene cijene. Zalihe su ograničene — naručite na vrijeme.'
            ),
        },
        'noviteti': {
            'seo_title': 'Noviteti opreme za ribolov | Novo u ponudi — opremazaribolov.ba',
            'meta_description': (
                'Novi artikli u ponudi: najnovija oprema za ribolov, brendovi i modeli. '
                'Otkrijte novitete i naručite online s brzim slanjem u BiH.'
            ),
            'h1_naslov': 'Noviteti — nova oprema za ribolov',
            'seo_tekst_iznad': (
                'Najnoviji proizvodi u našoj trgovini — redovno dodajemo nove modele i brendove.'
            ),
        },
        'about': {
            'seo_title': 'O nama | opremazaribolov.ba — oprema za ribolov iz prakse',
            'meta_description': (
                'Saznajte ko smo: dugogodišnje iskustvo u ribolovu i opremi, online shop za ribare '
                'u Bosni i Hercegovini. Kvalitet, savjet i pouzdana dostava.'
            ),
            'h1_naslov': 'O nama',
        },
        'payment': {
            'seo_title': 'Način plaćanja i dostava | opremazaribolov.ba',
            'meta_description': (
                'Plaćanje pouzećem, brza dostava poštom u roku do 48h i sigurno pakovanje. '
                'Sve o plaćanju i slanju na opremazaribolov.ba.'
            ),
            'h1_naslov': 'Način plaćanja i dostava',
        },
        'vlog': {
            'seo_title': 'Blog i vlog o ribolovu | Savjeti — opremazaribolov.ba',
            'meta_description': (
                'Blog i vlog: savjeti, priče i novosti iz svijeta ribolova. '
                'Korisni sadržaji za početnike i iskusne ribare — opremazaribolov.ba.'
            ),
            'h1_naslov': 'Blog i vlog',
            'seo_tekst_iznad': 'Savjeti, priče i novosti iz svijeta ribolova.',
        },
        'search': {
            'seo_title': 'Pretraga artikala | opremazaribolov.ba',
            'meta_description': 'Pronađite opremu za ribolov po nazivu, brendu ili šifri.',
            'h1_naslov': 'Rezultati pretrage',
        },
        'cart': {
            'seo_title': 'Korpa | opremazaribolov.ba',
            'meta_description': 'Pregled artikala u korpi prije narudžbe.',
            'h1_naslov': 'Korpa',
        },
        'checkout': {
            'seo_title': 'Narudžba | opremazaribolov.ba',
            'meta_description': 'Završite narudžbu — podaci za dostavu.',
            'h1_naslov': 'Narudžba',
        },
    }

    field_max = {
        'seo_title': 70,
        'meta_description': 160,
        'h1_naslov': 200,
    }

    for key, fields in defaults.items():
        obj, _ = PageSEO.objects.get_or_create(page_key=key)
        changed = False
        for field, value in fields.items():
            current = (getattr(obj, field, None) or '').strip()
            if not current and value:
                max_len = field_max.get(field)
                if max_len:
                    value = _clip(value, max_len)
                setattr(obj, field, value)
                changed = True
        if changed:
            obj.save()

    # SiteSettings global SEO fallbacks if empty
    for ss in SiteSettings.objects.all():
        updated = False
        if not (ss.seo_title or '').strip():
            ss.seo_title = _clip(
                'Oprema za ribolov | Online shop BiH — opremazaribolov.ba', 70,
            )
            updated = True
        if not (ss.meta_description or '').strip():
            ss.meta_description = _clip(
                'Online shop opreme za ribolov u BiH: štapovi, mašinice, varalice i pribor. '
                'Brza dostava, akcije i podrška — opremazaribolov.ba.',
                160,
            )
            updated = True
        if updated:
            ss.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0179_page_seo_and_entity_seo_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='seo_organizacija_naziv',
            field=models.CharField(
                blank=True,
                default='opremazaribolov.ba',
                help_text='Prikazuje se u Google Knowledge / Organization JSON-LD.',
                max_length=120,
                verbose_name='Naziv trgovine (schema.org)',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='seo_email',
            field=models.EmailField(
                blank=True,
                default='opremazaribolov.ba@gmail.com',
                help_text='Za schema.org ContactPoint (customer service).',
                max_length=254,
                verbose_name='SEO / kontakt email',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='seo_grad',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Opcionalno. Npr. Sarajevo — pomaže lokalnim upitima.',
                max_length=80,
                verbose_name='Grad (lokalni SEO)',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='seo_drzava',
            field=models.CharField(
                blank=True,
                default='BA',
                help_text='Dvoslovni kod za schema.org (BA = BiH).',
                max_length=2,
                verbose_name='Država (ISO, npr. BA)',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='seo_facebook_url',
            field=models.URLField(
                blank=True,
                default='https://www.facebook.com/opremazaribolov.ba',
                help_text='Puni link na Facebook stranicu (schema sameAs).',
                verbose_name='Facebook URL',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='seo_instagram_url',
            field=models.URLField(
                blank=True,
                default='',
                help_text='Opcionalno. Puni link na Instagram profil.',
                verbose_name='Instagram URL',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='google_site_verification',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    'Samo sadržaj content=„…“ iz Google meta taga '
                    '(Search Console → Ownership → HTML tag). '
                    'Npr. abc123XYZ — bez cijelog <meta> taga.'
                ),
                max_length=120,
                verbose_name='Google Search Console — verification code',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='seo_title_suffix',
            field=models.CharField(
                blank=True,
                default='opremazaribolov.ba',
                help_text=(
                    'Dodaje se na kraju title-a kad nije već unesen '
                    '(npr. „Naziv artikla | opremazaribolov.ba”). Prazno = bez sufiksa.'
                ),
                max_length=40,
                verbose_name='Sufiks u title tagu',
            ),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='seo_title',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Fallback <title> ako „SEO stranica → Početna” nije popunjena. '
                    'Preporuka: 50–60 znakova, ključne riječi ispred '
                    '(npr. Oprema za ribolov | Online shop BiH).'
                ),
                max_length=70,
                verbose_name='SEO title (početna) — fallback',
            ),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='meta_description',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Fallback opis za Google ako „SEO stranica → Početna” nije popunjena. '
                    'Preporuka: 140–160 znakova, jasan benefit + CTA (dostava, brendovi, BiH).'
                ),
                max_length=160,
                verbose_name='Meta description (početna) — fallback',
            ),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='og_image',
            field=models.ImageField(
                blank=True,
                help_text='Preporučeno 1200×630px. Kad se link dijeli na Facebook / WhatsApp / Viber.',
                null=True,
                upload_to='site/',
                verbose_name='Social share slika (OG image)',
            ),
        ),
        migrations.RunPython(seed_seo_defaults, noop_reverse),
    ]
