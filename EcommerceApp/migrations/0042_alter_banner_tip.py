from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0041_alter_homevlog_slika'),
    ]

    operations = [
        migrations.AlterField(
            model_name='banner',
            name='tip',
            field=models.CharField(
                choices=[
                    ('hero', 'Hero Carousel'),
                    ('grid', 'Grid Kartica (3 u redu ispod Hero)'),
                    ('featured', 'Featured Kartica'),
                    ('spotlight', 'Spotlight'),
                ],
                default='hero',
                max_length=20,
            ),
        ),
    ]