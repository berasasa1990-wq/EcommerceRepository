from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0180_seo_best_practice'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sitesettings',
            name='politika_dostava',
            field=models.TextField(
                blank=True,
                default='Dostava brzom poštom u roku od 48h.',
                help_text='Opcionalno. Tekst na stranici artikla ispod dugmeta korpe.',
                verbose_name='Uslovi dostave — tekst',
            ),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='politika_povrat',
            field=models.TextField(
                blank=True,
                default='Ukoliko je roba oštećena ili ne odgovara poručenoj, vršimo povrat.',
                help_text='Opcionalno.',
                verbose_name='Povrat robe — tekst',
            ),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='politika_garancija',
            field=models.TextField(
                blank=True,
                default='Garancija na kvalitet.',
                help_text='Opcionalno.',
                verbose_name='Garancija — tekst',
            ),
        ),
    ]
