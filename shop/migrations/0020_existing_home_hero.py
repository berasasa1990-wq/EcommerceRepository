from django.db import migrations


def create_existing_hero(apps, schema_editor):
    Banner = apps.get_model('shop', 'Banner')
    Banner.objects.using(schema_editor.connection.alias).get_or_create(
        position='hero',
        defaults={
            'title': 'Vaš svijet.\nUz Apple.',
            'eyebrow': 'TEHNOLOGIJA KOJA INSPIRIŠE',
            'subtitle': 'Od svakodnevnih ideja do velikih planova.\nOtkrijte iPhone, MacBook i dodatke koji vas prate.',
            'button_text': 'Istraži ponudu',
            'features': 'iPhone|MacBook|AirPods|Apple Watch',
            'url': '#proizvodi',
            'is_active': True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [('shop', '0019_banner_features')]
    operations = [migrations.RunPython(create_existing_hero, migrations.RunPython.noop)]
