from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0203_loyaltypurchase_placanje'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='packing_odstampana',
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text='Odštampan packing list (lokacije po kupcu). Skida se s liste narudžbi.',
                verbose_name='Packing odštampan',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='packing_odstampana_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Packing odštampan u',
            ),
        ),
    ]
