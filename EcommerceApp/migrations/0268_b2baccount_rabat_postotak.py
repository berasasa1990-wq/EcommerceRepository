from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0267_b2baccount_rabat'),
    ]

    operations = [
        migrations.AlterField(
            model_name='b2baccount',
            name='rabat',
            field=models.BooleanField(
                default=False,
                help_text='Uključite i unesite postotak. Kupac odmah vidi rabat i ostvaruje ga na cijeli račun.',
                verbose_name='Rabat',
            ),
        ),
        migrations.AddField(
            model_name='b2baccount',
            name='rabat_postotak',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Npr. 5 za −5%. Prikazuje se i odbija odmah, bez minimalnog iznosa.',
                max_digits=5,
                null=True,
                verbose_name='Rabat (%)',
            ),
        ),
    ]
