from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0042_alter_banner_tip'),
    ]

    operations = [
        migrations.AlterField(
            model_name='banner',
            name='tip',
            field=models.CharField(
                choices=[
                    ('hero', 'Hero Carousel'),
                    ('grid', 'Grid Kartica (2x2 ispod Hero)'),
                    ('featured', 'Featured Kartica'),
                    ('spotlight', 'Spotlight'),
                ],
                default='hero',
                max_length=20,
            ),
        ),
    ]