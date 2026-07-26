from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0158_home_brand_showcase'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='odstampana',
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text='Staff je odštampao narudžbu (račun + garancija + packing).',
                verbose_name='Odštampana',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='odstampana_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Odštampana u',
            ),
        ),
    ]
