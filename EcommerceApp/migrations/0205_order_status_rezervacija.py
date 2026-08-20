from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0204_order_packing_odstampana'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[
                    ('nova', 'Nova'),
                    ('rezervacija', 'Rezervacija'),
                    ('potvrdjena', 'Potvrđena'),
                    ('poslana', 'Poslana'),
                    ('zavrsena', 'Validatovana'),
                    ('otkazana', 'Otkazana'),
                ],
                default='nova',
                max_length=20,
            ),
        ),
    ]
