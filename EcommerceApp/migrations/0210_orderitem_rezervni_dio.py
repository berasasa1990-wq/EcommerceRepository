from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0209_magacinvpnarudzba_bulk'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='rezervni_dio',
            field=models.BooleanField(db_index=True, default=False, verbose_name='Rezervni dio'),
        ),
    ]
