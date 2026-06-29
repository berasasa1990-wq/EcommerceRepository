from django.db import migrations, models


def populate_variation_sifra(apps, schema_editor):
    ProductVariation = apps.get_model('EcommerceApp', 'ProductVariation')
    for variation in ProductVariation.objects.all():
        variation.sifra = f'VAR-{variation.pk:04d}'
        variation.save(update_fields=['sifra'])


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='productvariation',
            name='sifra',
            field=models.CharField(default='VAR-TEMP', max_length=50, verbose_name='Šifra'),
            preserve_default=False,
        ),
        migrations.RunPython(populate_variation_sifra, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='productvariation',
            name='sifra',
            field=models.CharField(max_length=50, unique=True, verbose_name='Šifra'),
        ),
    ]