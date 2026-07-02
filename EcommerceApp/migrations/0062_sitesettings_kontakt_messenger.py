from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0061_chat'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='kontakt_messenger',
            field=models.CharField(
                blank=True,
                help_text='Korisničko ime Facebook stranice za Messenger, npr. opremazaribolov.ba',
                max_length=120,
                verbose_name='Facebook Messenger',
            ),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='kontakt_telefon',
            field=models.CharField(
                blank=True,
                help_text='Broj za WhatsApp i Viber ikone (npr. +387 61 123 456). Prazno = koristi STORE_PHONE iz okruženja.',
                max_length=30,
                verbose_name='Kontakt telefon (WhatsApp / Viber)',
            ),
        ),
    ]