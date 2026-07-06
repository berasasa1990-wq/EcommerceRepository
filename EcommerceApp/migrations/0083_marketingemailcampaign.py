from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0082_product_olx_listing'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketingEmailCampaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naslov', models.CharField(max_length=200, verbose_name='Naslov emaila')),
                ('uvod', models.TextField(blank=True, help_text='Kratka poruka ispod bannera (opcionalno).', verbose_name='Uvodni tekst')),
                ('banner', models.ImageField(upload_to='marketing/', verbose_name='Banner slika')),
                ('cta_link', models.URLField(blank=True, help_text='Gdje vodi klik na banner / dugme. Prazno = akcijska ponuda na početnoj.', verbose_name='Link dugmeta')),
                ('cta_tekst', models.CharField(default='Pogledaj akcijsku ponudu', max_length=120, verbose_name='Tekst dugmeta')),
                ('status', models.CharField(choices=[('draft', 'Nacrt'), ('sent', 'Poslano'), ('failed', 'Greška')], default='draft', max_length=10)),
                ('broj_primaoca', models.PositiveIntegerField(default=0)),
                ('broj_gresaka', models.PositiveIntegerField(default=0)),
                ('poslano', models.DateTimeField(blank=True, null=True)),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('poslao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='marketing_kampanje', to=settings.AUTH_USER_MODEL, verbose_name='Poslao')),
            ],
            options={
                'verbose_name': 'Marketing email kampanja',
                'verbose_name_plural': 'Marketing email kampanje',
                'ordering': ['-kreirano'],
            },
        ),
    ]