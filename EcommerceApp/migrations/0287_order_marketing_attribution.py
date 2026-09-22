from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0286_coupon_iznos_coupon_vrsta_userprofile_prva_prijava_and_more')]

    operations = [
        migrations.AddField(model_name='order', name='marketing_source', field=models.CharField(blank=True, db_index=True, max_length=32, verbose_name='Marketing izvor')),
        migrations.AddField(model_name='order', name='marketing_device', field=models.CharField(blank=True, db_index=True, max_length=16, verbose_name='Marketing uređaj')),
    ]
