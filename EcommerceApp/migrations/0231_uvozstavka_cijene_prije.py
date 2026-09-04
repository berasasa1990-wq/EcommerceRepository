from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0230_warehousecustomer_vp_kupac'),
    ]

    operations = [
        migrations.AddField(
            model_name='uvozstavka',
            name='cijene_prije',
            field=models.JSONField(
                blank=True,
                help_text='MPC/VPC (i ostalo) na artiklu prije ovog uvoza, za nivelacije.',
                null=True,
                verbose_name='Cijene prije uvoza',
            ),
        ),
    ]
