import logging

from django.apps import AppConfig
from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)


def _configure_sqlite(sender, connection, **kwargs):
    """WAL + busy_timeout smanjuju 'database is locked' pri lokalnom runserveru."""
    if connection.vendor != 'sqlite':
        return
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL;')
        cursor.execute('PRAGMA busy_timeout=60000;')
        cursor.execute('PRAGMA synchronous=FULL;')


class EcommerceappConfig(AppConfig):
    name = 'EcommerceApp'

    def ready(self):
        connection_created.connect(_configure_sqlite)
        from django.contrib.auth.signals import user_logged_in
        from django.utils import timezone
        from .models import UserProfile

        def record_first_login(sender, request, user, **kwargs):
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if profile.prva_prijava is None:
                profile.prva_prijava = timezone.now()
                profile.save(update_fields=['prva_prijava'])

        user_logged_in.connect(record_first_login, dispatch_uid='record_first_customer_login')
        from django.db.models.signals import post_migrate
        from .retention_triggers import refresh_history_triggers
        post_migrate.connect(refresh_history_triggers, dispatch_uid='permanent_system_history')

        from django.db.models.signals import post_delete, post_save

        from .warehouse_customers_ledger import sync_customer_partner
        from .models import WarehouseCustomer
        post_save.connect(sync_customer_partner, sender=WarehouseCustomer, dispatch_uid='warehouse_customer_ledger')

        from .magacin import fold_stock_after_variation_delete
        from .models import ProductVariation

        post_delete.connect(fold_stock_after_variation_delete, sender=ProductVariation)

        from django.contrib import admin

        from .admin_forms import TurnstileAdminAuthenticationForm

        admin.site.login_form = TurnstileAdminAuthenticationForm
        admin.site.site_header = 'opremazaribolov.ba Admin'
        admin.site.site_title = 'opremazaribolov.ba'
        admin.site.index_title = 'Upravljanje trgovinom'

        from django.conf import settings

        if not settings.EMAIL_HOST_PASSWORD:
            if not settings.DEBUG:
                logger.error(
                    'EMAIL nije konfigurisan na produkciji — narudžbe neće stizati na %s.',
                    settings.ORDER_NOTIFICATION_EMAIL,
                )
            return

        logger.info(
            'Email SMTP spreman: %s preko %s:%s → narudžbe na %s',
            settings.EMAIL_HOST_USER,
            settings.EMAIL_HOST,
            settings.EMAIL_PORT,
            settings.ORDER_NOTIFICATION_EMAIL,
        )
