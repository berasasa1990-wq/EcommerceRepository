from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0169_chat_settings_and_product_offer'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='odoo_sale_order_id',
            field=models.PositiveIntegerField(
                blank=True,
                db_index=True,
                help_text='ID Sales narudžbe kreirane u Odoo iz web narudžbe.',
                null=True,
                verbose_name='Odoo sale.order ID',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='odoo_sale_order_name',
            field=models.CharField(
                blank=True,
                help_text='Npr. S00042 — broj u Odoo Sales.',
                max_length=40,
                verbose_name='Odoo SO broj',
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='odoo_sale_synced_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Odoo SO kreiran u',
            ),
        ),
    ]
