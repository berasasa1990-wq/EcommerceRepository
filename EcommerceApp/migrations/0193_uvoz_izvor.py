from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0192_warehousecustomer'),
    ]

    operations = [
        migrations.AddField(
            model_name='uvoz',
            name='izvor',
            field=models.CharField(
                choices=[('sajt', 'Sajt'), ('magacin', 'Magacin')],
                db_index=True,
                default='sajt',
                max_length=20,
                verbose_name='Izvor',
            ),
        ),
    ]
