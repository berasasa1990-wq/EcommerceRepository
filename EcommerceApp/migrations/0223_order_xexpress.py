from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0222_deklaracija_brend_polja'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='xexpress_poslano_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='X-Express poslano u'),
        ),
        migrations.AddField(
            model_name='order',
            name='xexpress_sifra',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Šifra pošiljke koju vrati X-Express API.',
                max_length=40,
                verbose_name='X-Express šifra',
            ),
        ),
    ]
