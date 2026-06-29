from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0012_loyalty_coupons'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='odoo_category_id',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                unique=True,
                verbose_name='Odoo category ID',
            ),
        ),
        migrations.AddField(
            model_name='product',
            name='odoo_template_id',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                unique=True,
                verbose_name='Odoo template ID',
            ),
        ),
        migrations.AddField(
            model_name='productvariation',
            name='odoo_variant_id',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                unique=True,
                verbose_name='Odoo variant ID',
            ),
        ),
    ]