# Rename advisor fish types → set types for the new advisor flow.

from django.db import migrations


def forwards(apps, schema_editor):
    FishType = apps.get_model('EcommerceApp', 'AdvisorBeginnerFishType')

    for code in ('smud', 'pastrmka', 'bijela', 'vise'):
        FishType.objects.filter(code=code).update(aktivan=False)

    saran = FishType.objects.filter(code='saran').first()
    if saran:
        # Avoid unique clash if saranski already exists
        if FishType.objects.filter(code='saranski').exclude(pk=saran.pk).exists():
            saran.aktivan = False
            saran.save(update_fields=['aktivan'])
        else:
            saran.code = 'saranski'
            saran.naziv = 'Saranski set'
            saran.emoji = saran.emoji or '🎣'
            saran.redoslijed = 10
            saran.aktivan = True
            saran.save()
    else:
        FishType.objects.update_or_create(
            code='saranski',
            defaults={
                'naziv': 'Saranski set',
                'emoji': '🎣',
                'redoslijed': 10,
                'aktivan': True,
            },
        )

    for code, naziv, emoji, red in (
        ('stuka', 'Lov štuke', '🐟', 20),
        ('som', 'Lov soma', '🐟', 21),
        ('ul', 'UL ribolov', '🎣', 22),
        ('feeder', 'Feeder set', '🎣', 30),
        ('plovak', 'Pečaljke za plovak', '🎣', 40),
    ):
        FishType.objects.update_or_create(
            code=code,
            defaults={
                'naziv': naziv,
                'emoji': emoji,
                'redoslijed': red,
                'aktivan': True,
            },
        )


def backwards(apps, schema_editor):
    # Best-effort restore of old labels
    FishType = apps.get_model('EcommerceApp', 'AdvisorBeginnerFishType')
    saranski = FishType.objects.filter(code='saranski').first()
    if saranski and not FishType.objects.filter(code='saran').exists():
        saranski.code = 'saran'
        saranski.naziv = 'Šaran'
        saranski.save()


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0139_live_visitor_savjetnik'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
