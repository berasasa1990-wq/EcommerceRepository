from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0201_magacinvpstavka_mp_ok'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='loyalty_napomena',
            field=models.TextField(blank=True, verbose_name='Loyalty napomena'),
        ),
    ]
