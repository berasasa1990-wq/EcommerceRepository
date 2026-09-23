from django.db import migrations


ALIASES = ('feeder stap', 'feder stap', 'feder stapo', 'stap', 'stapo', 'stapov', 'stapovi')


def add_feeder_aliases(apps, schema_editor):
    Category = apps.get_model('EcommerceApp', 'Category')
    for category in Category.objects.filter(naziv__iexact='Feeder stapovi'):
        current = [item.strip() for item in (category.search_tagovi or '').replace('\n', ',').split(',') if item.strip()]
        seen = {item.casefold() for item in current}
        current.extend(alias for alias in ALIASES if alias.casefold() not in seen)
        category.search_tagovi = ', '.join(current)
        category.save(update_fields=['search_tagovi'])


class Migration(migrations.Migration):
    dependencies = [('EcommerceApp', '0291_deployment_version')]

    operations = [migrations.RunPython(add_feeder_aliases, migrations.RunPython.noop)]
