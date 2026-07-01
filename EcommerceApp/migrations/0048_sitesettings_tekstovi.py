from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0047_product_proizvedeno_u_japanu'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='sitesettings',
            options={'verbose_name': 'Podešavanja', 'verbose_name_plural': 'Podešavanja'},
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='naslov_blog',
            field=models.CharField(default='Blogovi — Klik na željeni', max_length=200, verbose_name='Blog — naslov'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='naslov_izdvojeno',
            field=models.CharField(default='Izdvojeno', max_length=120, verbose_name='Izdvojeno — naslov'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='naslov_novo',
            field=models.CharField(default='Novo', max_length=120, verbose_name='Novo — naslov'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='podnaslov_izdvojeno',
            field=models.CharField(default='Odabrani artikli za vas', max_length=200, verbose_name='Izdvojeno — podnaslov'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='podnaslov_novo',
            field=models.CharField(default='Najnoviji artikli na sajtu', max_length=200, verbose_name='Novo — podnaslov'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='politika_dostava',
            field=models.TextField(default='Dostava brzom poštom u roku od 48h.', verbose_name='Uslovi dostave — tekst'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='politika_garancija',
            field=models.TextField(default='Garancija na kvalitet.', verbose_name='Garancija — tekst'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='politika_povrat',
            field=models.TextField(default='Ukoliko je roba oštećena ili ne odgovara poručenoj, vršimo povrat.', verbose_name='Povrat robe — tekst'),
        ),
    ]