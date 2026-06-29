from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0015_variation_odoo_template_stanje'),
    ]

    operations = [
        migrations.CreateModel(
            name='Tag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(max_length=50, unique=True)),
                ('slug', models.SlugField(blank=True, unique=True)),
            ],
            options={
                'verbose_name': 'Tag',
                'verbose_name_plural': 'Tagovi',
                'ordering': ['naziv'],
            },
        ),
        migrations.AddField(
            model_name='product',
            name='tagovi',
            field=models.ManyToManyField(blank=True, related_name='artikli', to='EcommerceApp.tag', verbose_name='Tagovi'),
        ),
    ]