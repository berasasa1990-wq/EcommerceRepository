from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0227_product_sakriven_do_stanja'),
    ]

    operations = [
        migrations.AddField(
            model_name='magacinvpnarudzba',
            name='placanje',
            field=models.CharField(
                blank=True,
                choices=[('gotovina', 'Gotovinski'), ('ziralno', 'Žiralno')],
                db_index=True,
                help_text='Žiralno se ne štampa na packing listi. Gotovinski ide na packing.',
                max_length=20,
                verbose_name='Plaćanje',
            ),
        ),
    ]
