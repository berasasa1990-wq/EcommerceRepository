from django.db import migrations, models


PAGE_KEYS = [
    'home',
    'cart',
    'checkout',
    'about',
    'payment',
    'vlog',
    'akcija',
    'noviteti',
    'search',
    'login',
    'register',
    'advisor',
    'order_success',
]


def seed_page_seo(apps, schema_editor):
    PageSEO = apps.get_model('EcommerceApp', 'PageSEO')
    for key in PAGE_KEYS:
        PageSEO.objects.get_or_create(page_key=key)


def unseed_page_seo(apps, schema_editor):
    PageSEO = apps.get_model('EcommerceApp', 'PageSEO')
    PageSEO.objects.filter(page_key__in=PAGE_KEYS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0178_chat_avatar_slika'),
    ]

    operations = [
        migrations.CreateModel(
            name='PageSEO',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('page_key', models.CharField(
                    choices=[
                        ('home', 'Početna'),
                        ('cart', 'Korpa'),
                        ('checkout', 'Narudžba (checkout)'),
                        ('about', 'O nama'),
                        ('payment', 'Način plaćanja'),
                        ('vlog', 'Blog / Vlog'),
                        ('akcija', 'Akcija (katalog)'),
                        ('noviteti', 'Noviteti (katalog)'),
                        ('search', 'Pretraga (rezultati)'),
                        ('login', 'Prijava'),
                        ('register', 'Registracija'),
                        ('advisor', 'Savjetnik'),
                        ('order_success', 'Uspješna narudžba'),
                    ],
                    max_length=40,
                    unique=True,
                    verbose_name='Stranica',
                )),
                ('seo_title', models.CharField(
                    blank=True,
                    help_text='Opcionalno. <title> i og:title. Preporučeno 50–60 znakova.',
                    max_length=70,
                    verbose_name='SEO title',
                )),
                ('meta_description', models.CharField(
                    blank=True,
                    help_text='Opcionalno. Opis u Google rezultatima. Do ~155–160 znakova.',
                    max_length=160,
                    verbose_name='Meta description',
                )),
                ('h1_naslov', models.CharField(
                    blank=True,
                    help_text='Opcionalno. Glavni naslov na stranici. Prazno = default tekst.',
                    max_length=200,
                    verbose_name='H1 naslov',
                )),
                ('seo_tekst_iznad', models.TextField(
                    blank=True,
                    help_text='Opcionalno. Tekst iznad liste proizvoda ili glavnog sadržaja.',
                    verbose_name='SEO tekst iznad sadržaja / proizvoda',
                )),
                ('seo_tekst_ispod', models.TextField(
                    blank=True,
                    help_text='Opcionalno. Tekst ispod liste proizvoda ili glavnog sadržaja.',
                    verbose_name='SEO tekst ispod sadržaja / proizvoda',
                )),
                ('azuriran', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'SEO stranice',
                'verbose_name_plural': 'SEO stranica',
                'ordering': ['page_key'],
            },
        ),
        migrations.AddField(
            model_name='brand',
            name='h1_naslov',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno. Prazno = naziv brenda.',
                max_length=200,
                verbose_name='H1 naslov',
            ),
        ),
        migrations.AddField(
            model_name='brand',
            name='meta_description',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno. Opis za Google kad se filtrira po brendu.',
                max_length=160,
                verbose_name='Meta description',
            ),
        ),
        migrations.AddField(
            model_name='brand',
            name='meta_title',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno. Prazno = naziv brenda.',
                max_length=70,
                verbose_name='SEO title',
            ),
        ),
        migrations.AddField(
            model_name='brand',
            name='seo_tekst_ispod',
            field=models.TextField(
                blank=True,
                help_text='Opcionalno.',
                verbose_name='SEO tekst ispod proizvoda',
            ),
        ),
        migrations.AddField(
            model_name='brand',
            name='seo_tekst_iznad',
            field=models.TextField(
                blank=True,
                help_text='Opcionalno.',
                verbose_name='SEO tekst iznad proizvoda',
            ),
        ),
        migrations.AddField(
            model_name='category',
            name='h1_naslov',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno. Prazno = naziv kategorije.',
                max_length=200,
                verbose_name='H1 naslov',
            ),
        ),
        migrations.AddField(
            model_name='category',
            name='seo_tekst_ispod',
            field=models.TextField(
                blank=True,
                help_text='Opcionalno. Tekst ispod liste artikala.',
                verbose_name='SEO tekst ispod proizvoda',
            ),
        ),
        migrations.AddField(
            model_name='category',
            name='seo_tekst_iznad',
            field=models.TextField(
                blank=True,
                help_text='Opcionalno. Tekst iznad liste artikala.',
                verbose_name='SEO tekst iznad proizvoda',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='h1_naslov',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno — prazno = naziv artikla.',
                max_length=200,
                verbose_name='H1 naslov',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='seo_tekst_ispod',
            field=models.TextField(
                blank=True,
                help_text='Opcionalno. Tekst ispod detalja artikla (npr. ispod opisa / povezanih).',
                verbose_name='SEO tekst ispod proizvoda',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='seo_tekst_iznad',
            field=models.TextField(
                blank=True,
                help_text='Opcionalno. Tekst iznad detalja artikla.',
                verbose_name='SEO tekst iznad proizvoda',
            ),
        ),
        migrations.AlterField(
            model_name='category',
            name='meta_description',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno. Kratak opis za Google i društvene mreže.',
                max_length=160,
                verbose_name='Meta description',
            ),
        ),
        migrations.AlterField(
            model_name='category',
            name='meta_title',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno. Ako ostaviš prazno koristi se naziv kategorije.',
                max_length=70,
                verbose_name='SEO title',
            ),
        ),
        migrations.AlterField(
            model_name='product',
            name='meta_description',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno — ostavi prazno za automatski opis koji počinje nazivom artikla.',
                max_length=160,
                verbose_name='Meta description',
            ),
        ),
        migrations.AlterField(
            model_name='product',
            name='meta_title',
            field=models.CharField(
                blank=True,
                help_text='Opcionalno — ostavi prazno za automatski (naziv artikla).',
                max_length=70,
                verbose_name='SEO title',
            ),
        ),
        migrations.RunPython(seed_page_seo, unseed_page_seo),
    ]
