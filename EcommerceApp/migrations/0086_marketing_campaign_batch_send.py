from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0085_rename_ecommerceap_aktivan_6f3e2a_idx_ecommerceap_aktivan_8e6884_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketingemailcampaign',
            name='slanje_lista',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='marketingemailcampaign',
            name='slanje_offset',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='marketingemailcampaign',
            name='slanje_ukupno',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='marketingemailcampaign',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Nacrt'),
                    ('sending', 'Slanje u toku'),
                    ('sent', 'Poslano'),
                    ('failed', 'Greška'),
                ],
                default='draft',
                max_length=10,
            ),
        ),
    ]