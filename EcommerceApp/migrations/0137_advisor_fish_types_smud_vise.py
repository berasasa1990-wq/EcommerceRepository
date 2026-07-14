from django.db import migrations


def seed_extra_fish(apps, schema_editor):
    FishType = apps.get_model('EcommerceApp', 'AdvisorBeginnerFishType')
    extra = [
        ('smud', 'Smuđ', '🐟', 25),
        ('vise', 'Više vrsta ribe', '🐟', 60),
    ]
    for code, naziv, emoji, red in extra:
        FishType.objects.get_or_create(
            code=code,
            defaults={'naziv': naziv, 'emoji': emoji, 'redoslijed': red, 'aktivan': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0136_advisor_beginner_sets'),
    ]

    operations = [
        migrations.RunPython(seed_extra_fish, migrations.RunPython.noop),
    ]
