from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0106_rename_ecommerceap_user_id_live_idx_ecommerceap_user_id_c577d3_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='livevisitoroffer',
            name='besplatna_dostava',
            field=models.BooleanField(
                default=False,
                help_text='Ako je uključeno, kupac na prvu narudžbu ostvaruje besplatnu dostavu.',
                verbose_name='Besplatna dostava (prva kupovina)',
            ),
        ),
    ]
