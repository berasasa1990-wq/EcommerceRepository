from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0202_userprofile_loyalty_napomena'),
    ]

    operations = [
        migrations.AddField(
            model_name='loyaltypurchase',
            name='placanje',
            field=models.CharField(
                choices=[('gotovina', 'Gotovina'), ('kartica', 'Kartica')],
                default='gotovina',
                max_length=12,
                verbose_name='Način plaćanja',
            ),
        ),
    ]
