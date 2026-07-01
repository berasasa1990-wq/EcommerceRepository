from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0053_alter_homevlog_slika'),
    ]

    operations = [
        migrations.AddField(
            model_name='banner',
            name='video',
            field=models.FileField(
                blank=True,
                help_text='Opcionalno. MP4/WebM/MOV, najviše 6 sekundi. Ako je postavljen, prikazuje se umjesto slike.',
                null=True,
                upload_to='banners/videos/',
                verbose_name='Video (max 6 s)',
            ),
        ),
    ]