from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0266_stock_movement_trace'),
    ]

    operations = [
        migrations.AddField(
            model_name='b2baccount',
            name='rabat',
            field=models.BooleanField(
                default=False,
                help_text='Ako je uključeno, kupac vidi rabat −5% i ostvaruje ga na račun preko 1500 KM netto.',
                verbose_name='Rabat',
            ),
        ),
    ]
