from decimal import Decimal

from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0268_b2baccount_rabat_postotak'),
    ]

    operations = [
        migrations.CreateModel(
            name='B2BAccountBrandRabat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('postotak', models.DecimalField(
                    decimal_places=2,
                    help_text='Npr. 5 za −5% na artikle ovog brenda.',
                    max_digits=5,
                    validators=[
                        django.core.validators.MinValueValidator(Decimal('0.01')),
                        django.core.validators.MaxValueValidator(Decimal('100')),
                    ],
                    verbose_name='Rabat (%)',
                )),
                ('account', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='brand_rabats',
                    to='EcommerceApp.b2baccount',
                    verbose_name='B2B korisnik',
                )),
                ('brand', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to='EcommerceApp.brand',
                    verbose_name='Brend',
                )),
            ],
            options={
                'verbose_name': 'Rabat po brendu',
                'verbose_name_plural': 'Rabati po brendovima',
                'ordering': ['brand__naziv'],
            },
        ),
        migrations.AddConstraint(
            model_name='b2baccountbrandrabat',
            constraint=models.UniqueConstraint(fields=('account', 'brand'), name='unique_b2b_account_brand_rabat'),
        ),
    ]
