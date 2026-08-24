from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0208_uvoz_listing_totals'),
    ]

    operations = [
        migrations.AddField(
            model_name='magacinvpnarudzba',
            name='bulk',
            field=models.BooleanField(default=False),
        ),
    ]
