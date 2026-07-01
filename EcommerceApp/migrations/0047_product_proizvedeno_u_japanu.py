from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0046_alter_sifra_max_length_200'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='proizvedeno_u_japanu',
            field=models.BooleanField(default=False, verbose_name='Proizvedeno u Japanu'),
        ),
    ]