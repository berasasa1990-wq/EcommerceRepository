from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0017_sitesettings_artikala_po_redu'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='prikazi_filter_na_pocetnoj',
            field=models.BooleanField(
                default=False,
                help_text='Uključuje filter sidebar lijevo od artikala na početnoj stranici.',
                verbose_name='Prikaži filter na početnoj',
            ),
        ),
    ]