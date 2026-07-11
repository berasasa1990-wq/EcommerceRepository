from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0104_staffsiteevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='livevisitor',
            name='pregledani_proizvodi',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Lista {id, naziv, views} — proizvodi koje je posjetilac otvorio u ovoj sesiji.',
                verbose_name='Pregledani proizvodi',
            ),
        ),
        migrations.AddField(
            model_name='livevisitor',
            name='izvor_dolaska',
            field=models.CharField(
                blank=True,
                help_text='facebook / google / instagram / direct / other',
                max_length=20,
                verbose_name='Izvor dolaska',
            ),
        ),
        migrations.AddIndex(
            model_name='livevisitor',
            index=models.Index(fields=['user', '-last_seen'], name='ecommerceap_user_id_live_idx'),
        ),
    ]
