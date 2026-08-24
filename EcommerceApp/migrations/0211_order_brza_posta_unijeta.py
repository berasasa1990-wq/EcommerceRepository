from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0210_orderitem_rezervni_dio'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='brza_posta_unijeta',
            field=models.BooleanField(
                db_index=True,
                default=False,
                verbose_name='Unijeto u Brzu poštu',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='brza_posta_unijeta_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Unijeto u Brzu poštu u',
            ),
        ),
    ]
