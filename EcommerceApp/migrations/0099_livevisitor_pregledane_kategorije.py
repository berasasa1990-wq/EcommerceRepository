from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0098_sitesettings_korpa_exit_popup'),
    ]

    operations = [
        migrations.AddField(
            model_name='livevisitor',
            name='pregledane_kategorije',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Nazivi kategorija koje je posjetilac pregledao u ovoj sesiji (najnovije prvo).',
                verbose_name='Pregledane kategorije',
            ),
        ),
    ]