import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0009_order_item_names'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('telefon', models.CharField(blank=True, max_length=30)),
                ('adresa', models.CharField(blank=True, max_length=300)),
                ('grad', models.CharField(blank=True, max_length=100)),
                ('postanski_broj', models.CharField(blank=True, max_length=20)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='profil', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Korisnički profil',
                'verbose_name_plural': 'Korisnički profili',
            },
        ),
        migrations.AddField(
            model_name='order',
            name='korisnik',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='narudzbe', to=settings.AUTH_USER_MODEL, verbose_name='Korisnik'),
        ),
    ]