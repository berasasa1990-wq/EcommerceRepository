from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0050_alter_banner_tip'),
    ]

    operations = [
        migrations.AlterField(
            model_name='product',
            name='slug',
            field=models.SlugField(blank=True, max_length=220, unique=True),
        ),
        migrations.AlterField(
            model_name='product',
            name='barkod',
            field=models.CharField(blank=True, max_length=200, verbose_name='Barkod'),
        ),
    ]