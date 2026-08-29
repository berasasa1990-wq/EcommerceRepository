from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0223_order_xexpress'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='boja_ikonica_korpa',
            field=models.CharField(
                blank=True,
                default='#111111',
                help_text='Kvadratna ikonica korpe na karticama (početna, katalog) i ikona u headeru. Hex npr. #111111',
                max_length=7,
                verbose_name='Boja ikonice korpe',
            ),
        ),
    ]
