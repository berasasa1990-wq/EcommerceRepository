from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0220_popis_odstampan'),
    ]

    operations = [
        migrations.CreateModel(
            name='MagacinDeklaracijaBrend',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(max_length=120, unique=True)),
                ('opis', models.TextField(verbose_name='Opis deklaracije')),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('azuriran', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Deklaracija brend',
                'verbose_name_plural': 'Deklaracije brendovi',
                'ordering': ['naziv', 'id'],
            },
        ),
    ]
