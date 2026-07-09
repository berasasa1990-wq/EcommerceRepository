import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0100_alter_sitesettings_korpa_exit_popup_aktivan_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='livevisitoroffer',
            name='aktivacioni_kod',
            field=models.CharField(blank=True, max_length=20, verbose_name='Aktivacioni kod'),
        ),
        migrations.AddField(
            model_name='livevisitoroffer',
            name='kod_aktiviran',
            field=models.BooleanField(default=False, verbose_name='Kod aktiviran'),
        ),
        migrations.AddField(
            model_name='livevisitoroffer',
            name='tip',
            field=models.CharField(
                choices=[('artikal', 'Artikal'), ('narudzba', 'Popust na narudžbu')],
                default='artikal',
                max_length=10,
                verbose_name='Tip ponude',
            ),
        ),
        migrations.AlterField(
            model_name='livevisitoroffer',
            name='product',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='live_visitor_offers',
                to='EcommerceApp.product',
                verbose_name='Artikal',
            ),
        ),
    ]