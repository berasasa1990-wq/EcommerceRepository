from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0186_variation_search_normalized'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='izvor',
            field=models.CharField(
                choices=[('webshop', 'Webshop'), ('magacin', 'Ručni unos (Magacin)')],
                db_index=True,
                default='webshop',
                max_length=20,
                verbose_name='Izvor',
            ),
        ),
    ]
