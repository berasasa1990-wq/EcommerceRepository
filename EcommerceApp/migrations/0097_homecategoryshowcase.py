import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0096_livevisitor_drzava'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeCategoryShowcase',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naslov', models.CharField(blank=True, help_text='Prazno = naziv kategorije.', max_length=120, verbose_name='Naslov sekcije')),
                ('redoslijed', models.PositiveIntegerField(default=0, verbose_name='Redoslijed')),
                ('aktivan', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('kategorija', models.ForeignKey(limit_choices_to={'aktivan': True}, on_delete=django.db.models.deletion.CASCADE, related_name='pocetna_sekcije', to='EcommerceApp.category', verbose_name='Kategorija')),
                ('postavke', models.ForeignKey(default=1, editable=False, on_delete=django.db.models.deletion.CASCADE, related_name='kategorije_na_pocetnoj', to='EcommerceApp.sitesettings')),
            ],
            options={
                'verbose_name': 'Kategorija na početnoj (2×2)',
                'verbose_name_plural': 'Kategorije na početnoj (2×2 mobil)',
                'ordering': ['redoslijed', 'id'],
            },
        ),
    ]