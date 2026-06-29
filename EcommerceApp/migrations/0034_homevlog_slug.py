from django.db import migrations, models
from django.utils.text import slugify


def populate_vlog_slugs(apps, schema_editor):
    HomeVlog = apps.get_model('EcommerceApp', 'HomeVlog')
    used_slugs = set()
    for vlog in HomeVlog.objects.order_by('id'):
        base_slug = slugify(vlog.naslov) or f'vlog-{vlog.pk}'
        slug = base_slug
        counter = 1
        while slug in used_slugs:
            slug = f'{base_slug}-{counter}'
            counter += 1
        vlog.slug = slug
        vlog.save(update_fields=['slug'])
        used_slugs.add(slug)


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0033_homevlog'),
    ]

    operations = [
        migrations.AddField(
            model_name='homevlog',
            name='slug',
            field=models.SlugField(blank=True, max_length=220),
        ),
        migrations.RunPython(populate_vlog_slugs, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='homevlog',
            name='slug',
            field=models.SlugField(blank=True, max_length=220, unique=True),
        ),
    ]