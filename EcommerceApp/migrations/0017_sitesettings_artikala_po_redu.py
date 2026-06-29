from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0016_product_tags'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='artikala_po_redu',
            field=models.PositiveSmallIntegerField(
                choices=[(3, '3 artikla u redu'), (4, '4 artikla u redu')],
                default=4,
                help_text='Broj artikala u jednom redu na početnoj i stranicama kategorija. Po stranici se prikazuje 4 reda.',
                verbose_name='Artikala u redu (katalog)',
            ),
        ),
    ]