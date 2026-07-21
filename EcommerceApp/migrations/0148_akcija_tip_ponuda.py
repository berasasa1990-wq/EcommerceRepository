# Generated manually for + Ponuda akcija tip

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0147_alter_aiprodajasettings_options_alter_akcija_tip'),
    ]

    operations = [
        migrations.AlterField(
            model_name='akcija',
            name='tip',
            field=models.CharField(
                choices=[
                    ('bundle', 'Pop-up bundle'),
                    ('qty_deal', 'Kupi više (količinski %)'),
                    ('ponuda', '+ Ponuda'),
                    ('ai_prodaja', 'AI prodaja / AI dwell'),
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
        migrations.AlterField(
            model_name='akcija',
            name='artikal',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Trigger artikal: kad ga kupac doda u korpu (za + Ponuda). '
                    'Za Pop-up bundle: samo ako je trigger „odabrani trigger artikal”. '
                    'Za „Kupi više”: artikal na koji važi količinski popust.'
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='akcije',
                to='EcommerceApp.product',
                verbose_name='Artikal',
            ),
        ),
        migrations.AlterField(
            model_name='akcija',
            name='gratis_artikal',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Za + Ponuda: artikal koji se nudi u popup-u nakon dodavanja trigger artikla. '
                    'Ako uneseš Popust (%), nudi se sa sniženjem; prazno = regularna cijena.'
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='akcije_gratis',
                to='EcommerceApp.product',
                verbose_name='Ponuda artikal',
            ),
        ),
        migrations.AlterField(
            model_name='akcija',
            name='popust_postotak',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text=(
                    'Pop-up bundle: % na cijeli set (ako linija nema svoj %). '
                    'Za % samo na jedan artikal — unesi „Popust % (samo ovaj artikal)” na bundle stavci. '
                    'Za + Ponuda: opcionalno — % snizenja na ponuđeni artikal; prazno = regularna cijena. '
                    'Za „Kupi više”: opcionalno — % po količini unosi se na tier linijama (2, 3…).'
                ),
                max_digits=5,
                null=True,
                verbose_name='Popust (%)',
            ),
        ),
    ]
