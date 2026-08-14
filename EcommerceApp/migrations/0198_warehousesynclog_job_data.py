from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0197_order_pick_claimed'),
    ]

    operations = [
        migrations.AddField(
            model_name='warehousesynclog',
            name='job_data',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Stanje chunkova da sync preživi reload i Render timeout.',
                verbose_name='Sync job',
            ),
        ),
    ]
