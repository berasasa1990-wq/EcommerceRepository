from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0095_livevisitor_grad'),
    ]

    operations = [
        migrations.AddField(
            model_name='livevisitor',
            name='drzava',
            field=models.CharField(blank=True, max_length=2, verbose_name='Država'),
        ),
    ]