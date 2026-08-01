# Generated manually — seed default fishing search synonyms

from django.db import migrations


def seed_synonyms(apps, schema_editor):
    SearchSynonymGroup = apps.get_model('EcommerceApp', 'SearchSynonymGroup')
    SearchSynonym = apps.get_model('EcommerceApp', 'SearchSynonym')

    # Inline normalize (migration must not import app code that may change)
    import re
    import unicodedata

    diac = str.maketrans({
        'š': 's', 'č': 'c', 'ć': 'c', 'ž': 'z',
        'Š': 's', 'Č': 'c', 'Ć': 'c', 'Ž': 'z',
    })

    def norm(value):
        if not value:
            return ''
        text = str(value).strip()
        text = text.replace('đ', 'dj').replace('Đ', 'dj')
        text = unicodedata.normalize('NFKD', text)
        text = ''.join(ch for ch in text if not unicodedata.combining(ch))
        text = text.translate(diac).casefold()
        text = re.sub(r'[\s_\-–—/\\|]+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # One row per unique normalizovani_pojam (stap≈štap handled by normalize)
    groups = [
        ('Štap', 100, ['štap', 'rod', 'pecaljka']),
        ('Mašinica', 100, ['mašinica', 'reel', 'rolna']),
        ('Najlon', 90, ['najlon', 'struna', 'monofil', 'line']),
        ('Varalica', 90, ['varalica', 'vobler', 'lure']),
        ('Feeder', 95, ['feeder', 'fider']),
        ('Šaran', 80, ['šaran', 'carp']),
        ('Som', 80, ['som', 'catfish']),
        ('Smuđ', 80, ['smuđ', 'smud', 'zander']),
        ('Štuka', 80, ['štuka', 'pike']),
        ('Šaranski', 85, ['šaranski']),
        ('Varaličarski', 85, ['varaličarski', 'spin', 'spinning']),
        ('Boila', 90, ['boila', 'boilie']),
        ('Udica', 70, ['udica', 'hook']),
        ('Plovak', 70, ['plovak', 'float']),
        ('Spod', 70, ['spod', 'spomb']),
        ('Hranilica', 70, ['hranilica', 'feeder hranilica']),
    ]

    for naziv, prioritet, terms in groups:
        group, _ = SearchSynonymGroup.objects.get_or_create(
            naziv=naziv,
            defaults={'aktivno': True, 'prioritet': prioritet},
        )
        existing = set(
            SearchSynonym.objects.filter(grupa=group).values_list(
                'normalizovani_pojam', flat=True,
            ),
        )
        for pojam in terms:
            n = norm(pojam)
            if not n or n in existing:
                continue
            SearchSynonym.objects.create(
                grupa=group,
                pojam=pojam,
                normalizovani_pojam=n,
            )
            existing.add(n)


def unseed_synonyms(apps, schema_editor):
    # Keep data on reverse — admin may have customized groups
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0171_search_synonym_models'),
    ]

    operations = [
        migrations.RunPython(seed_synonyms, unseed_synonyms),
    ]
