from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0231_uvozstavka_cijene_prije'),
    ]

    operations = [
        migrations.AddField(
            model_name='uvoz',
            name='popis_status',
            field=models.CharField(
                choices=[
                    ('nije', 'Nije popisano'),
                    ('u_toku', 'Popis u toku'),
                    ('zavrsen', 'Popis završen'),
                ],
                db_index=True,
                default='nije',
                max_length=12,
                verbose_name='Status popisa',
            ),
        ),
        migrations.AddField(
            model_name='uvoz',
            name='popis_zavrsen_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Popis završen'),
        ),
        migrations.AddField(
            model_name='uvoz',
            name='popis_zavrsio',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='zavrseni_uvoz_popisi',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Popis završio',
            ),
        ),
        migrations.AddField(
            model_name='uvoz',
            name='zaliha_primljena',
            field=models.BooleanField(
                default=True,
                help_text='Da li je količina već stavljena na lokaciju Uvoz.',
                verbose_name='Zaliha primljena',
            ),
        ),
        migrations.AddField(
            model_name='uvozstavka',
            name='popisano',
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                max_digits=12,
                null=True,
                verbose_name='Popisano',
            ),
        ),
        migrations.AddField(
            model_name='uvozstavka',
            name='popisano_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Popisano u'),
        ),
    ]
