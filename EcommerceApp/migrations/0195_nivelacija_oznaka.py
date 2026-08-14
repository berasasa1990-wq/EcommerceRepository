from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0194_rename_mg_customer_ime_idx_ecommerceap_ime_pre_9a564e_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='NivelacijaOznaka',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kljuc', models.CharField(db_index=True, max_length=220)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('kreirao', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='nivelacija_oznake',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('product', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='nivelacija_oznake',
                    to='EcommerceApp.product',
                )),
                ('uvoz', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='nivelacija_oznake',
                    to='EcommerceApp.uvoz',
                )),
            ],
            options={
                'verbose_name': 'Nivelacija oznaka',
                'verbose_name_plural': 'Nivelacija oznake',
            },
        ),
        migrations.AddConstraint(
            model_name='nivelacijaoznaka',
            constraint=models.UniqueConstraint(fields=('kljuc', 'uvoz'), name='uniq_nivelacija_kljuc_uvoz'),
        ),
    ]
