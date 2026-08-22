from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0205_order_status_rezervacija'),
    ]

    operations = [
        migrations.AlterField(
            model_name='magacinpopis',
            name='status',
            field=models.CharField(
                choices=[
                    ('u_toku', 'U toku'),
                    ('pauziran', 'Pauziran'),
                    ('zavrsen', 'Završen'),
                ],
                db_index=True,
                default='u_toku',
                max_length=20,
            ),
        ),
    ]
