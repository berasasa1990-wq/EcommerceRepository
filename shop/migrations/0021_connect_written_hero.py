from django.db import migrations


def connect_written_hero(apps, schema_editor):
    Banner = apps.get_model('shop', 'Banner')
    Banner.objects.using(schema_editor.connection.alias).filter(
        position='hero', is_active=False, title='Glavni banner',
        eyebrow='', subtitle='',
    ).update(
        title='Vaš svijet.\nUz Apple.',
        eyebrow='TEHNOLOGIJA KOJA INSPIRIŠE',
        subtitle='Od svakodnevnih ideja do velikih planova.\nOtkrijte iPhone, MacBook i dodatke koji vas prate.',
        button_text='Istraži ponudu',
        features='iPhone|MacBook|AirPods|Apple Watch',
        url='#proizvodi', is_active=True,
    )


class Migration(migrations.Migration):
    dependencies = [('shop', '0020_existing_home_hero')]
    operations = [migrations.RunPython(connect_written_hero, migrations.RunPython.noop)]
