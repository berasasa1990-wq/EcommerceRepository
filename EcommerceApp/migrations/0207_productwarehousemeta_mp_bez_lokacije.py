from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0206_magacin_popis_pauziran'),
    ]

    operations = [
        migrations.AddField(
            model_name='productwarehousemeta',
            name='mp_bez_lokacije',
            field=models.BooleanField(
                default=False,
                help_text='Artikal je fizički u maloprodaji i ostaje na sajtu i kad nema magacinske lokacije.',
                verbose_name='MP bez lokacije',
            ),
        ),
    ]
