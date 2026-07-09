from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0101_livevisitoroffer_narudzba_popust'),
    ]

    operations = [
        migrations.AlterField(
            model_name='livevisitoroffer',
            name='tip',
            field=models.CharField(
                choices=[
                    ('artikal', 'Artikal'),
                    ('narudzba', 'Popust na narudžbu'),
                    ('registracija', 'Registracija'),
                ],
                default='artikal',
                max_length=20,
                verbose_name='Tip ponude',
            ),
        ),
    ]
