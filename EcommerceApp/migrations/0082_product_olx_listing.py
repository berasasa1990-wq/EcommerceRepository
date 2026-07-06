from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0081_akcija_gratis_popup'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='olx_listing_id',
            field=models.PositiveIntegerField(
                blank=True, null=True, unique=True,
                verbose_name='OLX/Pik ID oglasa',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='olx_listing_slug',
            field=models.CharField(blank=True, max_length=220, verbose_name='OLX/Pik slug'),
        ),
        migrations.AddField(
            model_name='product',
            name='olx_listing_url',
            field=models.URLField(blank=True, verbose_name='OLX/Pik link'),
        ),
        migrations.AddField(
            model_name='product',
            name='olx_objavljen',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Objavljeno na OLX/Pik'),
        ),
    ]