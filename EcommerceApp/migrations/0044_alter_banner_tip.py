from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0043_alter_banner_tip'),
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