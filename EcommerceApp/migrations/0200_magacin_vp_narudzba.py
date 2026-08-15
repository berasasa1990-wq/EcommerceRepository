from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0199_magacin_popis'),
    ]

    operations = [
        migrations.CreateModel(
            name='MagacinVpNarudzba',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('u_toku', 'U toku'), ('zavrsena', 'Završena')], db_index=True, default='u_toku', max_length=20)),
                ('ime_prezime', models.CharField(blank=True, max_length=200)),
                ('telefon', models.CharField(blank=True, max_length=30)),
                ('adresa', models.CharField(blank=True, max_length=300)),
                ('grad', models.CharField(blank=True, max_length=100)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('postanski_broj', models.CharField(blank=True, max_length=20)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('azuriran', models.DateTimeField(auto_now=True)),
                ('zavrsen_at', models.DateTimeField(blank=True, null=True)),
                ('kreirao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='magacin_vp_narudzbe', to=settings.AUTH_USER_MODEL)),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='vp_narudzbe', to='EcommerceApp.warehousecustomer')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='vp_nacrti', to='EcommerceApp.order')),
            ],
            options={
                'verbose_name': 'Magacin VP narudžba',
                'verbose_name_plural': 'Magacin VP narudžbe',
                'ordering': ['-kreiran'],
            },
        ),
        migrations.CreateModel(
            name='MagacinVpStavka',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(max_length=200)),
                ('sifra', models.CharField(blank=True, max_length=200)),
                ('kolicina', models.PositiveIntegerField(default=1)),
                ('cijena', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('mpc', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('redoslijed', models.PositiveIntegerField(default=0)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('narudzba', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stavke', to='EcommerceApp.magacinvpnarudzba')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='vp_stavke', to='EcommerceApp.product')),
                ('variation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='vp_stavke', to='EcommerceApp.productvariation')),
            ],
            options={
                'verbose_name': 'VP stavka',
                'verbose_name_plural': 'VP stavke',
                'ordering': ['redoslijed', 'id'],
            },
        ),
    ]
