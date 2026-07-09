from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0097_homecategoryshowcase'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='korpa_exit_popup_aktivan',
            field=models.BooleanField(
                default=False,
                help_text='Prikazuje popup kad posjetilac pomjeri kursor prema zatvaranju taba na stranici korpe.',
                verbose_name='Korpa — exit popup aktivan',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='korpa_exit_popup_naslov',
            field=models.CharField(
                blank=True,
                default='Prije nego odete…',
                max_length=120,
                verbose_name='Korpa — exit popup naslov',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='korpa_exit_popup_tekst',
            field=models.TextField(
                blank=True,
                default='Završite narudžbu sada — artikli u korpi čekaju na vas.',
                verbose_name='Korpa — exit popup tekst',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='korpa_exit_popup_artikal',
            field=models.ForeignKey(
                blank=True,
                help_text='Opcionalno. Prikazuje se u popupu s dugmetom za dodavanje u korpu.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='korpa_exit_popupi',
                to='EcommerceApp.product',
                verbose_name='Korpa — exit popup artikal',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='korpa_exit_popup_popust',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Opcionalno. Popust na vrijednost korpe kad kupac prihvati ponudu (max 50%).',
                max_digits=5,
                null=True,
                verbose_name='Korpa — exit popup popust (%)',
            ),
        ),
    ]