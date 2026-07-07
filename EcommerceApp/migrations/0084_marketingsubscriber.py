from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0083_marketingemailcampaign'),
    ]

    operations = [
        migrations.CreateModel(
            name='MarketingSubscriber',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, unique=True, verbose_name='Email')),
                ('ime', models.CharField(blank=True, max_length=120, verbose_name='Ime')),
                ('aktivan', models.BooleanField(default=True, verbose_name='Aktivan')),
                ('izvor', models.CharField(
                    choices=[('manual', 'Ručno'), ('order', 'Narudžba'), ('import', 'Import')],
                    default='manual',
                    max_length=10,
                    verbose_name='Izvor',
                )),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('dodao', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='marketing_pretplatnici',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Dodao',
                )),
            ],
            options={
                'verbose_name': 'Marketing pretplatnik',
                'verbose_name_plural': 'Marketing pretplatnici',
                'ordering': ['-kreirano'],
            },
        ),
        migrations.AddIndex(
            model_name='marketingsubscriber',
            index=models.Index(fields=['aktivan'], name='EcommerceAp_aktivan_6f3e2a_idx'),
        ),
    ]