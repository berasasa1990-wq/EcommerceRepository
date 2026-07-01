import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class EcommerceappConfig(AppConfig):
    name = 'EcommerceApp'

    def ready(self):
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
