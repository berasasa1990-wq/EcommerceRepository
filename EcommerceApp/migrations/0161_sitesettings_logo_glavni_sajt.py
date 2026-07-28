from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0160_order_stanje_skinuto'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='logo_glavni_sajt',
            field=models.ImageField(
                blank=True,
                help_text=(
                    'Prikazuje se u headeru ispod glavnog loga uz tekst „by” '
                    '(pod-sajt / Carpologija BH). Automatski se skalira na ~200×48px PNG.'
                ),
                null=True,
                upload_to='site/',
                verbose_name='Logo glavnog sajta',
            ),
        ),
    ]
