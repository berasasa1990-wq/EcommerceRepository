"""Scheduled imports run outside HTTP requests."""
import logging
import threading

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 600


def run_import_cycle():
    close_old_connections()
    try:
        call_command('import_company_payments')
    finally:
        close_old_connections()


def run_worker(stop=None):
    stop = stop or threading.Event()
    while not stop.is_set():
        if settings.COMPANY_MAIL_APP_PASSWORD:
            try:
                run_import_cycle()
            except Exception:
                # Do not log credentials, server responses or email contents.
                logger.warning('Provjera uplata nije uspjela. Novi pokušaj za 10 minuta.')
        if stop.wait(INTERVAL_SECONDS):
            break
