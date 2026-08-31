from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0224_sitesettings_boja_ikonica_korpa'),
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
                    ('akcijska', 'Akcijska ponuda'),
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
        migrations.AddField(
            model_name='akcija',
            name='flash_trigger',
            field=models.CharField(
                blank=True,
                choices=[
                    ('product', 'Odabrani artikal'),
                    ('category', 'Kategorija'),
                    ('offer_product', 'Artikal iz akcijske ponude'),
                ],
                default='offer_product',
                help_text=(
                    'Artikal = samo odabrani trigger artikal. '
                    'Kategorija = svaki artikal iz te kategorije. '
                    'Artikal iz ponude = bilo koji od do 4 artikla u ponudi.'
                ),
                max_length=20,
                verbose_name='Trigger akcijske ponude',
            ),
        ),
        migrations.AddField(
            model_name='akcija',
            name='flash_naslov',
            field=models.CharField(
                blank=True,
                help_text='Prazno = „POSEBNA PONUDA – SAMO SADA!”',
                max_length=120,
                verbose_name='Naslov ponude',
            ),
        ),
        migrations.AddField(
            model_name='akcija',
            name='flash_podnaslov',
            field=models.CharField(
                blank=True,
                help_text='Prazno = automatski tekst s preostalim vremenom.',
                max_length=220,
                verbose_name='Podnaslov ponude',
            ),
        ),
        migrations.CreateModel(
            name='AkcijaFlashLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('popust_postotak', models.DecimalField(
                    blank=True,
                    decimal_places=2,
                    help_text='Opcionalno. Prazno = % akcijske ponude.',
                    max_digits=5,
                    null=True,
                    verbose_name='Popust %',
                )),
                ('redoslijed', models.PositiveSmallIntegerField(default=0, verbose_name='Redoslijed')),
                ('akcija', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='flash_lines',
                    to='EcommerceApp.akcija',
                    verbose_name='Akcija',
                )),
                ('product', models.ForeignKey(
                    limit_choices_to={'aktivan': True, 'na_stanju': True},
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='akcija_flash_lines',
                    to='EcommerceApp.product',
                    verbose_name='Artikal',
                )),
            ],
            options={
                'verbose_name': 'Artikal akcijske ponude',
                'verbose_name_plural': 'Artikli akcijske ponude',
                'ordering': ['redoslijed', 'id'],
            },
        ),
    ]
