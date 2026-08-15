from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0198_warehousesynclog_job_data'),
    ]

    operations = [
        migrations.CreateModel(
            name='MagacinPopis',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('u_toku', 'U toku'), ('zavrsen', 'Završen')], db_index=True, default='u_toku', max_length=20)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('azuriran', models.DateTimeField(auto_now=True)),
                ('zavrsen_at', models.DateTimeField(blank=True, null=True)),
                ('kreirao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='magacin_popisi', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Magacin popis',
                'verbose_name_plural': 'Magacin popisi',
                'ordering': ['-kreiran'],
            },
        ),
        migrations.CreateModel(
            name='MagacinPopisStavka',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(max_length=200)),
                ('sifra', models.CharField(blank=True, max_length=200)),
                ('kolicina', models.PositiveIntegerField(default=1)),
                ('redoslijed', models.PositiveIntegerField(default=0)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('popis', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stavke', to='EcommerceApp.magacinpopis')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='popis_stavke', to='EcommerceApp.product')),
                ('variation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='popis_stavke', to='EcommerceApp.productvariation')),
            ],
            options={
                'verbose_name': 'Popis stavka',
                'verbose_name_plural': 'Popis stavke',
                'ordering': ['redoslijed', 'id'],
            },
        ),
    ]
