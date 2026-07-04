from django.db import migrations, models
import django.db.models.deletion


def migrate_popup_and_deals(apps, schema_editor):
    Akcija = apps.get_model('EcommerceApp', 'Akcija')
    Popup = apps.get_model('EcommerceApp', 'Popup')
    UpsellOffer = apps.get_model('EcommerceApp', 'UpsellOffer')

    for popup in Popup.objects.all():
        if popup.tip == 'slika':
            tip = 'slika'
        elif popup.akcija_prag_iznos is not None and popup.akcija_popust_postotak is not None:
            tip = 'uslov'
        else:
            tip = 'timer'
        Akcija.objects.create(
            naziv=popup.naziv,
            tip=tip,
            slika=popup.slika,
            artikal_id=popup.akcija_artikal_id,
            popust_postotak=popup.akcija_popust_postotak,
            prag_korpe_km=popup.akcija_prag_iznos,
            pocetak=popup.akcija_pocetak,
            trajanje_sati=popup.akcija_sati,
            tekst_dugmeta=popup.tekst_dugmeta,
            link_dugmeta=popup.link_dugmeta or '',
            boja_dugmeta=popup.boja_dugmeta,
            boja_opisa=popup.boja_akcija_istice,
            aktivan=popup.aktivan,
            za_prijavljene=popup.za_prijavljene,
            za_neprijavljene=popup.za_neprijavljene,
            redoslijed=popup.redoslijed,
            ponovo_poslije_dana=popup.ponovo_poslije_dana,
            popup_delay_seconds=popup.popup_delay_seconds,
        )

    for offer in UpsellOffer.objects.exclude(deal_artikal__isnull=True).exclude(deal_vrsta=''):
        Akcija.objects.create(
            naziv=offer.naziv or f'X+1 — {offer.deal_artikal_id}',
            tip='x_plus_1',
            artikal_id=offer.deal_artikal_id,
            deal_vrsta=offer.deal_vrsta,
            popust_postotak=offer.deal_popust,
            aktivan=offer.aktivan,
            redoslijed=offer.redoslijed,
            tekst_dugmeta=offer.tekst_dugmeta or 'Dodaj u korpu',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0071_remove_banner_kategorije_banner_kategorija_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Akcija',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(help_text='Samo za prepoznavanje u adminu.', max_length=100, verbose_name='Interni naziv')),
                ('tip', models.CharField(choices=[('slika', 'Pop-up + akcija + slika'), ('timer', 'Akcija + tajmer'), ('x_plus_1', 'X+1 prodaja (samo korpa)'), ('uslov', 'Uslov prodaja')], default='slika', max_length=12, verbose_name='Tip akcije')),
                ('slika', models.ImageField(blank=True, help_text='Obavezno za tip „Pop-up + akcija + slika”.', null=True, upload_to='akcije/', verbose_name='Slika')),
                ('popust_postotak', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, verbose_name='Popust (%)')),
                ('prag_korpe_km', models.DecimalField(blank=True, decimal_places=2, help_text='Minimalni iznos u korpi (bez ovog artikla) za uslovnu prodaju.', max_digits=10, null=True, verbose_name='Uslov: iznos u korpi (KM)')),
                ('deal_vrsta', models.CharField(blank=True, choices=[('1+1', '1+1 (kupi 1, drugi snižen)'), ('2+1', '2+1 (kupi 2, treći snižen)'), ('3+1', '3+1 (kupi 3, četvrti snižen)')], max_length=10, null=True, verbose_name='Vrsta X+1')),
                ('pocetak', models.DateTimeField(blank=True, null=True, verbose_name='Početak akcije')),
                ('trajanje_sati', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Trajanje akcije (sati)')),
                ('tekst_dugmeta', models.CharField(default='Saznaj više', max_length=50, verbose_name='Tekst dugmeta')),
                ('link_dugmeta', models.CharField(blank=True, help_text='Prazno = stranica artikla ili /registracija/.', max_length=300, verbose_name='Link dugmeta')),
                ('boja_dugmeta', models.CharField(default='#5BB805', max_length=7, verbose_name='Boja dugmeta')),
                ('boja_opisa', models.CharField(default='#5BB805', help_text='Boja teksta opisa / tajmera / poruke.', max_length=7, verbose_name='Boja opisa')),
                ('aktivan', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('za_prijavljene', models.BooleanField(default=False, verbose_name='Prikaži prijavljenim korisnicima')),
                ('za_neprijavljene', models.BooleanField(default=True, verbose_name='Prikaži neprijavljenim korisnicima')),
                ('redoslijed', models.PositiveIntegerField(default=0, verbose_name='Redoslijed')),
                ('ponovo_poslije_dana', models.PositiveSmallIntegerField(default=7, help_text='Koliko dana ne prikazivati pop-up nakon zatvaranja.', verbose_name='Ponovo prikaži poslije (dana)')),
                ('popup_delay_seconds', models.PositiveSmallIntegerField(default=5, help_text='0 = odmah. Ne vrijedi za X+1 (samo korpa).', verbose_name='Prikaži pop-up nakon (sekundi)')),
                ('artikal', models.ForeignKey(blank=True, help_text='Aktivan artikal sa sajta (tajmer, uslov, X+1).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='akcije', to='EcommerceApp.product', verbose_name='Artikal')),
            ],
            options={
                'verbose_name': 'Akcija',
                'verbose_name_plural': 'Akcije',
                'ordering': ['redoslijed', '-id'],
            },
        ),
        migrations.RunPython(migrate_popup_and_deals, migrations.RunPython.noop),
    ]