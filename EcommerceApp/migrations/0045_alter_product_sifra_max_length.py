from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0044_alter_banner_tip'),
    ]

    operations = [
        migrations.AlterField(
            model_name='orderitem',
            name='sifra',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='product',
            name='sifra',
            field=models.CharField(
                blank=True, max_length=100, null=True, unique=True, verbose_name='Šifra',
            ),
        ),
        migrations.AlterField(
            model_name='productvariation',
            name='sifra',
            field=models.CharField(
                blank=True, max_length=100, null=True, unique=True, verbose_name='Šifra',
            ),
        ),
    ]