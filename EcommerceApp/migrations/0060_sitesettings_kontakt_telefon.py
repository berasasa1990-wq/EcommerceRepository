from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0059_banner_kategorija_optional_link'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='kontakt_telefon',
            field=models.CharField(
                blank=True,
                help_text='Broj za plutajuću ikonu poruke (npr. +387 61 123 456). Prazno = koristi STORE_PHONE iz okruženja.',
                max_length=30,
                verbose_name='Kontakt telefon (WhatsApp)',
            ),
        ),
    ]