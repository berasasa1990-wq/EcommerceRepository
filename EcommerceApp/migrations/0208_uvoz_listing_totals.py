from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0207_productwarehousemeta_mp_bez_lokacije'),
    ]

    operations = [
        migrations.AddField(
            model_name='uvoz',
            name='broj_mpc_promjena',
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name='Promjena MPC'),
        ),
        migrations.AddField(
            model_name='uvoz',
            name='ukupna_fakturna',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=14, null=True, verbose_name='Ukupna fakturna',
            ),
        ),
    ]
