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
        cursor.execute('PRAGMA synchronous=NORMAL;')


class EcommerceappConfig(AppConfig):
    name = 'EcommerceApp'

    def ready(self):
        connection_created.connect(_configure_sqlite)

        from django.contrib import admin

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
