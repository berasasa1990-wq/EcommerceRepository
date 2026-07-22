# Generated manually for prioritet_lagera (Redukovanje lagera)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0156_tekst_dugme_korpa'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='prioritet_lagera',
            field=models.PositiveSmallIntegerField(
                choices=[
                    (0, 'Normalno'),
                    (1, 'Favorizuj'),
                    (2, 'Hit redukovanje lagera'),
                ],
                db_index=True,
                default=0,
                help_text=(
                    'Prioritet među relevantnim rezultatima (pretraga, kategorija, preporuke). '
                    'Nikad ne gura nerelevantne artikle. '
                    'Normalno = bez boosta; Favorizuj = blago; '
                    'Hit redukovanje lagera = maksimalni prioritet.'
                ),
                verbose_name='Redukovanje lagera',
            ),
        ),
    ]
