from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0107_livevisitoroffer_besplatna_dostava'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrizeWheel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(help_text='Samo za prepoznavanje u adminu.', max_length=100, verbose_name='Interni naziv')),
                ('aktivan', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('audience', models.CharField(choices=[('all', 'Svi posjetioci'), ('registered', 'Samo registrovani')], default='all', max_length=20, verbose_name='Prikaži')),
                ('prize_type', models.CharField(choices=[('product', 'Artikal (100% popust)'), ('percent', 'Popust % na narudžbu'), ('fixed_km', 'Popust u KM'), ('free_shipping', 'Besplatna dostava')], default='percent', max_length=20, verbose_name='Tip nagrade')),
                ('discount_percent', models.DecimalField(blank=True, decimal_places=2, help_text='Za tip „Popust % na narudžbu” (jednokratno).', max_digits=5, null=True, verbose_name='Popust %')),
                ('discount_km', models.DecimalField(blank=True, decimal_places=2, help_text='Za tip „Popust u KM” — umanjuje korpu jednom.', max_digits=10, null=True, verbose_name='Popust KM')),
                ('win_chance_percent', models.DecimalField(decimal_places=2, default=Decimal('15.00'), help_text='Koliko % okretanja pada na nagradu (ostatak = promašaj). Npr. 20 = svaki 5. otprilike.', max_digits=5, verbose_name='Šansa za nagradu (%)')),
                ('segments_count', models.PositiveSmallIntegerField(default=8, help_text='Ukupno segmenata (1 nagrada + ostalo promašaj). Preporuka 6–12.', verbose_name='Broj polja na točku')),
                ('naslov', models.CharField(default='Nagradna igra', max_length=120, verbose_name='Naslov popupa')),
                ('podnaslov', models.CharField(blank=True, default='Zavrti točak i osvoji nagradu!', max_length=200, verbose_name='Podnaslov')),
                ('popup_delay_seconds', models.PositiveSmallIntegerField(default=4, help_text='0 = odmah.', verbose_name='Prikaži nakon (sekundi)')),
                ('once_per_visitor', models.BooleanField(default=True, help_text='Ako je uključeno, posjetilac može zavrtjeti samo jednom (po sesiji / nalogu).', verbose_name='Jednom po posjetiocu')),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('azurirano', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(blank=True, help_text='Za tip „Artikal” — kupac dobija 100% popust (gratis).', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='prize_wheels', to='EcommerceApp.product', verbose_name='Artikal (nagrada)')),
            ],
            options={
                'verbose_name': 'Nagradni točak',
                'verbose_name_plural': 'Nagradni točkovi',
                'ordering': ['-aktivan', '-azurirano'],
            },
        ),
        migrations.CreateModel(
            name='PrizeWheelSpin',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(blank=True, db_index=True, max_length=40, verbose_name='Sesija')),
                ('won', models.BooleanField(default=False, verbose_name='Dobio nagradu')),
                ('prize_type', models.CharField(blank=True, max_length=20, verbose_name='Tip nagrade')),
                ('discount_percent', models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='Popust %')),
                ('discount_km', models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='Popust KM')),
                ('segment_index', models.PositiveSmallIntegerField(default=0, verbose_name='Segment')),
                ('reward_claimed', models.BooleanField(default=False, verbose_name='Nagrada primijenjena')),
                ('reward_consumed', models.BooleanField(default=False, verbose_name='Iskorišteno na narudžbi')),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='prize_wheel_spins', to='EcommerceApp.product')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='prize_wheel_spins', to=settings.AUTH_USER_MODEL, verbose_name='Korisnik')),
                ('wheel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='spins', to='EcommerceApp.prizewheel', verbose_name='Točak')),
            ],
            options={
                'verbose_name': 'Spin nagradnog točka',
                'verbose_name_plural': 'Spinovi nagradnog točka',
                'ordering': ['-kreirano'],
            },
        ),
        migrations.AddIndex(
            model_name='prizewheelspin',
            index=models.Index(fields=['session_key', 'wheel'], name='EcommerceAp_session_pw_idx'),
        ),
        migrations.AddIndex(
            model_name='prizewheelspin',
            index=models.Index(fields=['user', 'wheel'], name='EcommerceAp_user_pw_idx'),
        ),
    ]
