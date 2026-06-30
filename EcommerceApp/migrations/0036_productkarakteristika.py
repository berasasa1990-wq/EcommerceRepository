from django.db import migrations, models
import django.db.models.deletion

DEFAULT_KARAKTERISTIKE = (
    ('Garancija', ''),
    ('Kvalitet', ''),
)


def dodaj_default_karakteristike(apps, schema_editor):
    Product = apps.get_model('EcommerceApp', 'Product')
    ProductKarakteristika = apps.get_model('EcommerceApp', 'ProductKarakteristika')
    for product in Product.objects.all().iterator():
        for redoslijed, (naziv, vrijednost) in enumerate(DEFAULT_KARAKTERISTIKE):
            ProductKarakteristika.objects.get_or_create(
                artikal_id=product.pk,
                naziv=naziv,
                defaults={'vrijednost': vrijednost, 'redoslijed': redoslijed},
            )


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0035_alter_homevlog_options_alter_homevlog_naslov_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductKarakteristika',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(max_length=120, verbose_name='Naziv')),
                ('vrijednost', models.TextField(blank=True, verbose_name='Vrijednost')),
                ('redoslijed', models.PositiveIntegerField(default=0, verbose_name='Redoslijed')),
                ('artikal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='karakteristike', to='EcommerceApp.product', verbose_name='Artikal')),
            ],
            options={
                'verbose_name': 'Karakteristika',
                'verbose_name_plural': 'Karakteristike',
                'ordering': ['redoslijed', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='productkarakteristika',
            constraint=models.UniqueConstraint(fields=('artikal', 'naziv'), name='unique_karakteristika_po_artiklu'),
        ),
        migrations.RunPython(dodaj_default_karakteristike, migrations.RunPython.noop),
    ]