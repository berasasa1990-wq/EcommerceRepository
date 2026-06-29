from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0014_alter_sitesettings_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='productvariation',
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
            name='stanje',
            field=models.PositiveIntegerField(default=0, verbose_name='Količina'),
        ),
    ]