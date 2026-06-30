from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0040_alter_banner_tip'),
    ]

    operations = [
        migrations.AlterField(
            model_name='homevlog',
            name='slika',
            field=models.ImageField(
                help_text='Upload: konvertuje se u AVIF (max 30KB). Prikazuje se na početnoj (3 u redu) i na stranici vloga.',
                upload_to='vlogs/',
                verbose_name='Slika',
            ),
        ),
    ]