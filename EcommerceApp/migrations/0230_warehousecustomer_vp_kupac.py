from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0229_banner_mobile_help_1080x1350'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousecustomer',
            name='vp_kupac',
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text='Narudžbe ovog kupca idu kao VP narudžbe.',
                verbose_name='VP kupac',
            ),
        ),
    ]
