import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0270_b2b_live_proxy'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MagacinAkcija',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('broj', models.CharField(editable=False, max_length=20, unique=True)),
                ('broj_nivelacije', models.CharField(max_length=40, verbose_name='Broj nivelacije')),
                ('popust_postotak', models.DecimalField(decimal_places=2, max_digits=5, verbose_name='Popust (%)')),
                ('akcija_do', models.DateField(blank=True, null=True, verbose_name='Akcija važi do')),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('kreirao', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='magacin_akcije',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Magacin akcija',
                'verbose_name_plural': 'Magacin akcije',
                'ordering': ['-kreiran', '-id'],
            },
        ),
        migrations.CreateModel(
            name='MagacinAkcijaStavka',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(max_length=300)),
                ('sifra', models.CharField(blank=True, max_length=200)),
                ('stara_cijena', models.DecimalField(decimal_places=2, max_digits=10)),
                ('nova_cijena', models.DecimalField(decimal_places=2, max_digits=10)),
                ('popust_postotak', models.DecimalField(decimal_places=2, max_digits=5)),
                ('redoslijed', models.PositiveIntegerField(default=0)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('akcija', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stavke',
                    to='EcommerceApp.magacinakcija',
                )),
                ('product', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='akcija_stavke',
                    to='EcommerceApp.product',
                )),
                ('variation', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='akcija_stavke',
                    to='EcommerceApp.productvariation',
                )),
            ],
            options={
                'verbose_name': 'Stavka akcije',
                'verbose_name_plural': 'Stavke akcije',
                'ordering': ['redoslijed', 'id'],
            },
        ),
    ]
