from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0111_alter_prizewheel_options_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='OnlineGiftCampaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(help_text='Samo za admin (npr. „Vikend online poklon”).', max_length=100, verbose_name='Interni naziv')),
                ('aktivan', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('audience', models.CharField(choices=[('all', 'Svi online posjetioci'), ('registered', 'Samo registrovani online')], default='all', max_length=20, verbose_name='Kome prikazati')),
                ('prize_type', models.CharField(choices=[('product', 'Gratis artikal (100%)'), ('percent', '% na kompletnu narudžbu'), ('fixed_km', 'KM iznos popusta'), ('free_shipping', 'Besplatna dostava')], default='percent', max_length=20, verbose_name='Tip nagrade')),
                ('discount_percent', models.DecimalField(blank=True, decimal_places=2, help_text='Za tip % na narudžbu (jednokratno).', max_digits=5, null=True, verbose_name='Popust %')),
                ('discount_km', models.DecimalField(blank=True, decimal_places=2, help_text='Za tip fiksni KM popust (jednokratno).', max_digits=10, null=True, verbose_name='Popust KM')),
                ('win_chance_percent', models.DecimalField(decimal_places=2, default=Decimal('30.00'), help_text='Koliko % online posjetilaca dobije nagradu (ostali vide „sreću drugi put”).', max_digits=5, verbose_name='Šansa za nagradu (%)')),
                ('naslov', models.CharField(default='Online nagrada za tebe!', max_length=120, verbose_name='Naslov')),
                ('poruka', models.CharField(blank=True, default='Kao hvala što ste na sajtu — otkrijte da li ste dobili poklon.', max_length=220, verbose_name='Poruka')),
                ('popup_delay_seconds', models.PositiveSmallIntegerField(default=3, help_text='0 = odmah.', verbose_name='Prikaži nakon (sekundi)')),
                ('only_tracked_online', models.BooleanField(default=False, help_text='Ako je uključeno, nagrada se nudi samo onima koje vidiš u Uživo analitici (LiveVisitor).', verbose_name='Samo praćeni online posjetioci')),
                ('once_per_visitor', models.BooleanField(default=True, verbose_name='Jednom po posjetiocu')),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('azurirano', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(blank=True, help_text='Za tip gratis artikal.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='online_gift_campaigns', to='EcommerceApp.product', verbose_name='Artikal (gratis)')),
            ],
            options={
                'verbose_name': 'Online nagrada',
                'verbose_name_plural': 'Online nagrade',
                'ordering': ['-aktivan', '-azurirano'],
            },
        ),
        migrations.CreateModel(
            name='OnlineGiftClaim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(blank=True, db_index=True, max_length=40)),
                ('won', models.BooleanField(default=False)),
                ('prize_type', models.CharField(blank=True, max_length=20)),
                ('discount_percent', models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ('discount_km', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('reward_claimed', models.BooleanField(default=False)),
                ('reward_consumed', models.BooleanField(default=False)),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('campaign', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='claims', to='EcommerceApp.onlinegiftcampaign', verbose_name='Kampanja')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='online_gift_claims', to='EcommerceApp.product')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='online_gift_claims', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Online nagrada (pokušaj)',
                'verbose_name_plural': 'Online nagrade (pokušaji)',
                'ordering': ['-kreirano'],
            },
        ),
        migrations.AddIndex(
            model_name='onlinegiftclaim',
            index=models.Index(fields=['session_key', 'campaign'], name='EcommerceAp_session_ogc_idx'),
        ),
        migrations.AddIndex(
            model_name='onlinegiftclaim',
            index=models.Index(fields=['user', 'campaign'], name='EcommerceAp_user_ogc_idx'),
        ),
        migrations.DeleteModel(name='PrizeWheelSpin'),
        migrations.DeleteModel(name='PrizeWheel'),
    ]
