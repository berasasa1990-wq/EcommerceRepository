from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0211_order_brza_posta_unijeta'),
    ]

    operations = [
        migrations.AlterField(
            model_name='warehousestock',
            name='variation',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='magacin_zalihe',
                to='EcommerceApp.productvariation',
            ),
        ),
    ]
