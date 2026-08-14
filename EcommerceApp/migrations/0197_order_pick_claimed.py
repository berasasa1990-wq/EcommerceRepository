from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0196_orderitem_pokupljeno'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='pick_claimed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='preuzete_narudzbe',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Picking preuzeo',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='pick_claimed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Picking preuzeto'),
        ),
        migrations.AddField(
            model_name='order',
            name='pick_claimed_name',
            field=models.CharField(blank=True, max_length=120, verbose_name='Picking preuzeo (ime)'),
        ),
    ]
