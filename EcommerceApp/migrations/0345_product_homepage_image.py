from django.db import migrations, models
from django.db.models import F


def copy_new_arrivals_images(apps, schema_editor):
    Product = apps.get_model('EcommerceApp', 'Product')
    Product.objects.using(schema_editor.connection.alias).filter(
        slika_pocetna='',
    ).exclude(slika_novo='').update(slika_pocetna=F('slika_novo'))


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0344_unique_home_video_uploads')]

    operations = [
        migrations.RenameField('product', 'slika_izdvojena', 'slika_pocetna'),
        migrations.AlterField(
            model_name='product', name='slika_pocetna',
            field=models.ImageField(
                upload_to='products/homepage/', blank=True,
                verbose_name='Slika za početnu stranicu',
                help_text='Koristi se samo na početnoj stranici, u svim sekcijama. Prazno = postojeća slika artikla.',
            ),
        ),
        migrations.RunPython(copy_new_arrivals_images, migrations.RunPython.noop),
        migrations.RemoveField('product', 'slika_novo'),
    ]
