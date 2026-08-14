from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0195_nivelacija_oznaka'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='pick_state',
            field=models.JSONField(blank=True, default=dict, verbose_name='Picking stanje'),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='kolicina_pokupljeno',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='Količina potvrđena na pickingu. Ako je manja, faktura ide po ovoj količini.',
                null=True,
                verbose_name='Pokupljeno',
            ),
        ),
    ]
