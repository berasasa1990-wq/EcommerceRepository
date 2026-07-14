# Akcije: samo Pop-up bundle + Kupi više; AI prodaja kao proxy admin model

from django.db import migrations, models


def deactivate_legacy_akcije(apps, schema_editor):
    Akcija = apps.get_model('EcommerceApp', 'Akcija')
    Akcija.objects.exclude(tip__in=['bundle', 'qty_deal']).update(aktivan=False)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0133_sitesettings_product_dwell_artikli'),
    ]

    operations = [
        migrations.CreateModel(
            name='AIProdajaSettings',
            fields=[
            ],
            options={
                'verbose_name': 'AI prodaja',
                'verbose_name_plural': 'AI prodaja',
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('EcommerceApp.sitesettings',),
        ),
        migrations.AlterField(
            model_name='akcija',
            name='tip',
            field=models.CharField(
                choices=[
                    ('bundle', 'Pop-up bundle'),
                    ('qty_deal', 'Kupi više (količinski %)'),
                    ('slika', 'Pop-up + slika (zastarjelo)'),
                    ('timer', 'Akcija + tajmer (zastarjelo)'),
                    ('x_plus_1', 'X+1 prodaja (zastarjelo)'),
                    ('uslov', 'Uslov prodaja (zastarjelo)'),
                    ('korpa_nudjenje', 'Korpa nudjenje (zastarjelo)'),
                    ('gratis', '+ Gratis (zastarjelo)'),
                ],
                default='bundle',
                max_length=16,
                verbose_name='Tip akcije',
            ),
        ),
        migrations.RunPython(deactivate_legacy_akcije, noop_reverse),
    ]
