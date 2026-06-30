from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0045_alter_product_sifra_max_length'),
    ]

    operations = [
        migrations.AlterField(
            model_name='orderitem',
            name='sifra',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='product',
            name='sifra',
            field=models.CharField(
                blank=True, max_length=200, null=True, unique=True, verbose_name='Šifra',
            ),
        ),
        migrations.AlterField(
            model_name='productvariation',
            name='sifra',
            field=models.CharField(
                blank=True, max_length=200, null=True, unique=True, verbose_name='Šifra',
            ),
        ),
    ]