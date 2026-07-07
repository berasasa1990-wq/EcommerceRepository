from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0094_livevisitoroffer'),
    ]

    operations = [
        migrations.AddField(
            model_name='livevisitor',
            name='grad',
            field=models.CharField(blank=True, max_length=100, verbose_name='Grad'),
        ),
        migrations.AddField(
            model_name='livevisitor',
            name='ip_adresa',
            field=models.GenericIPAddressField(blank=True, null=True, verbose_name='IP adresa'),
        ),
    ]