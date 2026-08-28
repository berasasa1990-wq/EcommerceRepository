from django.db import migrations, models


def copy_opis_to_uvoznik(apps, schema_editor):
    Model = apps.get_model('EcommerceApp', 'MagacinDeklaracijaBrend')
    for row in Model.objects.all():
        opis = (getattr(row, 'opis', None) or '').strip()
        if opis and not (row.uvoznik or '').strip():
            row.uvoznik = opis[:200]
            row.save(update_fields=['uvoznik'])


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0221_magacin_deklaracija_brend'),
    ]

    operations = [
        migrations.AddField(
            model_name='magacindeklaracijabrend',
            name='adresa',
            field=models.CharField(blank=True, max_length=300, verbose_name='Adresa'),
        ),
        migrations.AddField(
            model_name='magacindeklaracijabrend',
            name='godina_uvoza',
            field=models.CharField(blank=True, max_length=20, verbose_name='Godina uvoza'),
        ),
        migrations.AddField(
            model_name='magacindeklaracijabrend',
            name='telefon',
            field=models.CharField(blank=True, max_length=40, verbose_name='Telefon'),
        ),
        migrations.AddField(
            model_name='magacindeklaracijabrend',
            name='uvoznik',
            field=models.CharField(blank=True, max_length=200, verbose_name='Uvoznik'),
        ),
        migrations.AddField(
            model_name='magacindeklaracijabrend',
            name='zemlja_izvoza',
            field=models.CharField(blank=True, max_length=80, verbose_name='Zemlja izvoza'),
        ),
        migrations.AddField(
            model_name='magacindeklaracijabrend',
            name='zemlja_porijekla',
            field=models.CharField(blank=True, max_length=80, verbose_name='Zemlja porijekla'),
        ),
        migrations.AlterField(
            model_name='magacindeklaracijabrend',
            name='naziv',
            field=models.CharField(max_length=120, unique=True, verbose_name='Naziv'),
        ),
        migrations.RunPython(copy_opis_to_uvoznik, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='magacindeklaracijabrend',
            name='opis',
        ),
    ]
