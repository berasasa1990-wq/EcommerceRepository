# Generated manually for online gift automatic/manual mode + order link

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0113_rename_ecommerceap_session_ogc_idx_ecommerceap_session_8165db_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='onlinegiftcampaign',
            name='automatic',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Uključeno: popup se automatski prikaže svima na sajtu (jednom po posjetiocu). '
                    'Isključeno: nagrada se ne pojavljuje sama — staff je pušta ručno u Uživo analitici '
                    'pored kupca.'
                ),
                verbose_name='Automatski svima online',
            ),
        ),
        migrations.AddField(
            model_name='onlinegiftclaim',
            name='order',
            field=models.ForeignKey(
                blank=True,
                help_text='Popunjava se kad kupac iskoristi nagradu u checkoutu.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='online_gift_claims',
                to='EcommerceApp.order',
                verbose_name='Narudžba',
            ),
        ),
        migrations.AddIndex(
            model_name='onlinegiftclaim',
            index=models.Index(fields=['won', '-kreirano'], name='ecommerceap_won_kre_idx'),
        ),
        migrations.CreateModel(
            name='OnlineGiftPush',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(db_index=True, max_length=40, verbose_name='Sesija')),
                ('played', models.BooleanField(default=False, verbose_name='Otvorio nagradu')),
                ('dismissed', models.BooleanField(default=False, verbose_name='Zatvorio')),
                ('kreirano', models.DateTimeField(auto_now_add=True)),
                ('azurirano', models.DateTimeField(auto_now=True)),
                ('campaign', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pushes',
                    to='EcommerceApp.onlinegiftcampaign',
                    verbose_name='Kampanja',
                )),
                ('staff', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='online_gift_pushes_sent',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Staff',
                )),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='online_gift_pushes',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Kupac',
                )),
            ],
            options={
                'verbose_name': 'Online nagrada (ručno)',
                'verbose_name_plural': 'Online nagrade (ručno)',
                'ordering': ['-kreirano'],
            },
        ),
        migrations.AddIndex(
            model_name='onlinegiftpush',
            index=models.Index(
                fields=['session_key', 'campaign', 'played'],
                name='ecommerceap_session_ogp_idx',
            ),
        ),
    ]
