from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0010_user_auth_orders'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='besplatna_dostava_od',
            field=models.DecimalField(decimal_places=2, default=Decimal('250.00'), help_text='Narudžbe iznad ovog iznosa imaju besplatnu dostavu.', max_digits=10, verbose_name='Besplatna dostava od (KM)'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='dostava_cijena',
            field=models.DecimalField(decimal_places=2, default=Decimal('11.00'), max_digits=10, verbose_name='Cijena dostave (KM)'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='dostava_naziv',
            field=models.CharField(default='xExpress Brza Pošta', max_length=100, verbose_name='Naziv dostave'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='novi_korisnik_besplatna_dostava',
            field=models.BooleanField(default=False, help_text='Primjenjuje se na prvu narudžbu registrovanog korisnika.', verbose_name='Novi korisnici — besplatna dostava'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='novi_korisnik_popust_km',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Opcionalno. Fiksni iznos popusta na prvu narudžbu.', max_digits=10, null=True, verbose_name='Novi korisnici — popust (KM)'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='novi_korisnik_popust_postotak',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Opcionalno. Npr. unesite 10 za 10% popusta na prvu narudžbu.', max_digits=5, null=True, verbose_name='Novi korisnici — popust (%)'),
        ),
        migrations.AddField(
            model_name='order',
            name='dostava',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
        ),
        migrations.AddField(
            model_name='order',
            name='medjuzbir',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
        ),
        migrations.AddField(
            model_name='order',
            name='popust',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10),
        ),
        migrations.RunPython(
            lambda apps, schema_editor: _populate_existing_orders(apps),
            migrations.RunPython.noop,
        ),
    ]


def _populate_existing_orders(apps):
    Order = apps.get_model('EcommerceApp', 'Order')
    for order in Order.objects.all():
        if order.medjuzbir == Decimal('0.00'):
            order.medjuzbir = order.ukupno
            order.save(update_fields=['medjuzbir'])