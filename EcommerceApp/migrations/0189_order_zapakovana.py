from django.db import migrations, models
from django.utils import timezone


def mark_validated_as_packed(apps, schema_editor):
    Order = apps.get_model('EcommerceApp', 'Order')
    Order.objects.filter(lager_status='validirano', zapakovana=False).update(
        zapakovana=True,
        zapakovana_at=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0188_order_lager_hold'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='zapakovana',
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text='Validirana narudžba je automatski skinuta s pakovanja.',
                verbose_name='Zapakovana',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='zapakovana_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Zapakovana u'),
        ),
        migrations.RunPython(mark_validated_as_packed, migrations.RunPython.noop),
    ]
