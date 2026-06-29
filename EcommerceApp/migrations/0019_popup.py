from decimal import Decimal

from django.db import migrations, models


def kreiraj_loyalty_popup(apps, schema_editor):
    Popup = apps.get_model('EcommerceApp', 'Popup')
    if Popup.objects.exists():
        return
    Popup.objects.create(
        naziv='Loyalty registracija',
        naslov='Ostvarite popuste pri svakoj kupovini',
        tekst=(
            'Registrujte se i aktivirajte loyalty karticu. '
            'Skupljajte bodove i ostvarujte popuste pri svakoj narudžbi.'
        ),
        aktivan=True,
        za_prijavljene=False,
        za_neprijavljene=True,
        popust_postotak=Decimal('10.00'),
        tekst_dugmeta='Registruj se',
        link_dugmeta='/registracija/',
        redoslijed=0,
        ponovo_poslije_dana=7,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0018_sitesettings_prikazi_filter'),
    ]

    operations = [
        migrations.CreateModel(
            name='Popup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(help_text='Samo za prepoznavanje u adminu.', max_length=100, verbose_name='Interni naziv')),
                ('naslov', models.CharField(max_length=200, verbose_name='Naslov')),
                ('tekst', models.TextField(verbose_name='Tekst')),
                ('aktivan', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('za_prijavljene', models.BooleanField(default=False, verbose_name='Prikaži prijavljenim korisnicima')),
                ('za_neprijavljene', models.BooleanField(default=True, verbose_name='Prikaži neprijavljenim korisnicima')),
                ('popust_postotak', models.DecimalField(blank=True, decimal_places=2, help_text='Opcionalno — prikazuje se u pop-upu.', max_digits=5, null=True, verbose_name='Popust (%)')),
                ('popust_km', models.DecimalField(blank=True, decimal_places=2, help_text='Opcionalno — prikazuje se u pop-upu.', max_digits=10, null=True, verbose_name='Popust (KM)')),
                ('tekst_dugmeta', models.CharField(default='Registruj se', max_length=50, verbose_name='Tekst dugmeta')),
                ('link_dugmeta', models.CharField(blank=True, help_text='Npr. /registracija/ ili puni URL. Prazno = stranica za registraciju.', max_length=300, verbose_name='Link dugmeta')),
                ('redoslijed', models.PositiveIntegerField(default=0, verbose_name='Redoslijed')),
                ('ponovo_poslije_dana', models.PositiveSmallIntegerField(default=7, help_text='Koliko dana ne prikazivati nakon što korisnik zatvori pop-up.', verbose_name='Ponovo prikaži poslije (dana)')),
            ],
            options={
                'verbose_name': 'Pop-up',
                'verbose_name_plural': 'Pop-upi',
                'ordering': ['redoslijed', '-id'],
            },
        ),
        migrations.RunPython(kreiraj_loyalty_popup, migrations.RunPython.noop),
    ]