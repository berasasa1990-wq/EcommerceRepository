import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('EcommerceApp', '0271_magacin_akcija'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='StockNotify',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254)),
                ('kreiran', models.DateTimeField(auto_now_add=True)),
                ('notified_at', models.DateTimeField(blank=True, null=True)),
                ('product', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='stock_notifies',
                    to='EcommerceApp.product',
                )),
                ('user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='stock_notifies',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Obavijest o stanju',
                'verbose_name_plural': 'Obavijesti o stanju',
            },
        ),
        migrations.AddIndex(
            model_name='stocknotify',
            index=models.Index(fields=['product', 'notified_at'], name='stocknotify_prod_note_idx'),
        ),
        migrations.AddIndex(
            model_name='stocknotify',
            index=models.Index(fields=['email'], name='stocknotify_email_idx'),
        ),
        migrations.AddConstraint(
            model_name='stocknotify',
            constraint=models.UniqueConstraint(
                condition=models.Q(notified_at__isnull=True),
                fields=('product', 'email'),
                name='uniq_stock_notify_pending_email',
            ),
        ),
    ]
