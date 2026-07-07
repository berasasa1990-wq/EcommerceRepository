from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0087_marketingemailcampaign_slanje_poslati'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketingSubscriberGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naziv', models.CharField(max_length=80, verbose_name='Naziv grupe')),
                ('redoslijed', models.PositiveIntegerField(default=0, verbose_name='Redoslijed')),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('dodao', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='marketing_grupe',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Kreirao',
                )),
            ],
            options={
                'verbose_name': 'Marketing grupa',
                'verbose_name_plural': 'Marketing grupe',
                'ordering': ['redoslijed', 'id'],
            },
        ),
        migrations.AddField(
            model_name='marketingsubscriber',
            name='grupa',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pretplatnici',
                to='EcommerceApp.marketingsubscribergroup',
                verbose_name='Grupa',
            ),
        ),
        migrations.AddField(
            model_name='marketingemailcampaign',
            name='slanje_grupa',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='kampanje',
                to='EcommerceApp.marketingsubscribergroup',
                verbose_name='Posljednja odabrana grupa',
            ),
        ),
        migrations.AddField(
            model_name='marketingemailcampaign',
            name='slanje_ukljuci_registrovane',
            field=models.BooleanField(default=False),
        ),
    ]