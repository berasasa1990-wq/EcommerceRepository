from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0162_userprofile_telefon_verifikovan'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='search_tagovi',
            field=models.TextField(
                blank=True,
                help_text=(
                    'Riječi za pretragu na sajtu, odvojene zarezom '
                    '(npr. masinica, masince, rola, role). '
                    'Vrijedi za ovu kategoriju/podkategoriju i artikle u njoj.'
                ),
                verbose_name='Search tagovi',
            ),
        ),
    ]
