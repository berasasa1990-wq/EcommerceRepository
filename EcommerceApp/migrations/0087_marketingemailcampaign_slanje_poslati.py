from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0086_marketing_campaign_batch_send'),
    ]

    operations = [
        migrations.AddField(
            model_name='marketingemailcampaign',
            name='slanje_poslati',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Email adrese kojima je kampanja uspješno poslana (bez duplikata pri nastavku).',
            ),
        ),
    ]