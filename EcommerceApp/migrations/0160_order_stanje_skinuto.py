from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0159_order_odstampana'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='stanje_skinuto',
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text='Zaliha skinuta iz Odoa (zapakovano i poslato — Brza pošta).',
                verbose_name='Stanje skinuto',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='stanje_skinuto_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Stanje skinuto u',
            ),
        ),
    ]
