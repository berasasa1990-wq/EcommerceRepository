from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('EcommerceApp', '0060_sitesettings_kontakt_telefon'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatConversation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_key', models.CharField(blank=True, db_index=True, max_length=40)),
                ('guest_name', models.CharField(blank=True, max_length=120)),
                ('guest_email', models.EmailField(blank=True, max_length=254)),
                ('status', models.CharField(choices=[('open', 'Otvoren'), ('closed', 'Zatvoren')], default='open', max_length=10)),
                ('staff_unread_count', models.PositiveIntegerField(default=0)),
                ('customer_unread_count', models.PositiveIntegerField(default=0)),
                ('last_message_at', models.DateTimeField(auto_now_add=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='chat_conversations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Chat razgovor',
                'verbose_name_plural': 'Chat razgovori',
                'ordering': ['-last_message_at'],
            },
        ),
        migrations.CreateModel(
            name='ChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sender_type', models.CharField(choices=[('customer', 'Kupac'), ('staff', 'Podrška')], max_length=10)),
                ('body', models.TextField(max_length=2000)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('read_by_staff', models.BooleanField(default=False)),
                ('read_by_customer', models.BooleanField(default=False)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='EcommerceApp.chatconversation')),
                ('staff_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='chat_replies', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Chat poruka',
                'verbose_name_plural': 'Chat poruke',
                'ordering': ['created_at'],
            },
        ),
    ]