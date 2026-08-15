from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0200_magacin_vp_narudzba'),
    ]

    operations = [
        migrations.AddField(
            model_name='magacinvpstavka',
            name='mp_ok',
            field=models.BooleanField(default=False),
        ),
    ]
