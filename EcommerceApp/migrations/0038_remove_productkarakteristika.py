from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0037_alter_product_opis'),
    ]

    operations = [
        migrations.DeleteModel(
            name='ProductKarakteristika',
        ),
        migrations.AlterField(
            model_name='product',
            name='opis',
            field=models.TextField(
                blank=True,
                help_text='Prikazuje se na stranici artikla.',
                verbose_name='Opis',
            ),
        ),
    ]