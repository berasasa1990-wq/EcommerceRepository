import django.db.models.deletion
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0011_shipping_pogodnosti'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoyaltyCard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kod', models.CharField(max_length=20, unique=True, verbose_name='Online kod')),
                ('barkod', models.CharField(max_length=20, unique=True, verbose_name='Barkod')),
                ('nivo', models.CharField(choices=[('bronza', 'Bronza'), ('srebrna', 'Srebrna'), ('zlatna', 'Zlatna'), ('platinum', 'Platinum')], default='bronza', max_length=20)),
                ('ukupna_potrosnja', models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=12)),
                ('kreirana', models.DateTimeField(auto_now_add=True)),
                ('azurirana', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='loyalty_kartica', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Loyalty kartica',
                'verbose_name_plural': 'Loyalty kartice',
            },
        ),
        migrations.CreateModel(
            name='Coupon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kod', models.CharField(max_length=20, unique=True)),
                ('naziv', models.CharField(max_length=100)),
                ('postotak', models.DecimalField(decimal_places=2, max_digits=5)),
                ('aktivan', models.BooleanField(default=True)),
                ('automatski', models.BooleanField(default=False, help_text='Kreiran i ažuriran iz loyalty kartice.', verbose_name='Automatski (loyalty)')),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('loyalty_kartica', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='kupon', to='EcommerceApp.loyaltycard')),
                ('vlasnik', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='kuponi', to=settings.AUTH_USER_MODEL, verbose_name='Vlasnik (samo on može koristiti)')),
            ],
            options={
                'verbose_name': 'Kupon',
                'verbose_name_plural': 'Kuponi',
            },
        ),
        migrations.AddField(
            model_name='order',
            name='kupon_kod',
            field=models.CharField(blank=True, max_length=20),
        ),
    ]