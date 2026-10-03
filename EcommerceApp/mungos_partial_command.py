"""Manual partial sync; only integration metadata may be persisted."""
import math
import time
from contextlib import nullcontext
from urllib.parse import urlsplit
from django.conf import settings

from .management.commands.mungos_bulk_sync import STAGING_URL, retry_seconds
from .mungos_diagnostics import safe_api_error

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from .models import Product, MungosProductMapping
from .mungos_bulk_lock import bulk_lock
from .mungos_client import MungosClient, MungosError
from .mungos_partial import build_partial_payload
from .mungos_product import build_product_preview, sanitized_json

BLOCKED = {'IN_FLIGHT', 'UNKNOWN_REMOTE_STATE', 'UNKNOWN'}


class StopSyncRun(CommandError):
    """Auth or configuration failures must stop all partial PUTs."""


class PartialSyncCommand(BaseCommand):
    requires_system_checks = []
    operation = None

    def add_arguments(self, parser):
        parser.add_argument('--product-id', type=int)
        parser.add_argument('--confirm', action='store_true')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--start-after-id', type=int, default=0)
        parser.add_argument('--delay', type=float, default=1.0)

    def handle(self, *args, **options):
        if settings.MUNGOS_BASE_URL != STAGING_URL:
            raise StopSyncRun('STAGING ONLY | Potreban je tačan staging connector URL.')
        if (not math.isfinite(options['delay']) or options['delay'] < 1
                or options['start_after_id'] < 0
                or (options['limit'] is not None and options['limit'] < 1)
                or (options['product_id'] is not None and options['product_id'] < 1)):
            raise CommandError('Delay mora biti najmanje 1s; limit/product-id pozitivan; start-after-id nenegativan.')
        self.last_request = None
        products = Product.objects.select_related('kategorija', 'mungos_mapping').prefetch_related(
            'varijacije', 'dodatne_slike').order_by('pk')
        single = options['product_id'] is not None
        if single:
            products = products.filter(pk=options['product_id'])
            if not products.exists():
                raise CommandError('NOT_SENT | Proizvod ne postoji.')
        else:
            products = products.filter(pk__gt=options['start_after_id'], mungos_mapping__mungos_uuid__isnull=False
                                       ).exclude(mungos_mapping__last_sync_status__in=BLOCKED)
            if options['limit'] is not None:
                products = products[:options['limit']]
        if not options['confirm']:
            self.stdout.write('DRY RUN | bez HTTP-a i upisa.')
        failures = 0
        with bulk_lock() if options['confirm'] else nullcontext():
            for product in products.iterator(chunk_size=200):
                try:
                    self.sync(product, options)
                except StopSyncRun:
                    raise
                except CommandError:
                    if single:
                        raise
                    failures += 1
                    # Errors are reported by sync; never expose arbitrary exception text.
                    self.stdout.write(sanitized_json(dict(product_id=product.pk, result='FAILED')))
        if failures:
            raise CommandError(f'Bulk završen | {failures} neuspješnih sync pokušaja; nema CREATE fallbacka.')

    def sync(self, product, options):
        mapping = getattr(product, 'mungos_mapping', None)
        if not mapping or not mapping.mungos_uuid or mapping.last_sync_status in BLOCKED:
            self.stdout.write('SKIPPED | UNKNOWN/unmapped; nema HTTP-a niti CREATE fallbacka.')
            return
        payload = build_partial_payload(build_product_preview(product), self.operation)
        if payload is None:
            self.stdout.write('SKIPPED | Varijante ili payload zahtijevaju pregled; nema HTTP-a.')
            return
        self.stdout.write(sanitized_json(dict(product_id=product.pk, operation=self.operation,
                                             mungos_uuid=str(mapping.mungos_uuid), payload=payload)))
        if not options['confirm']:
            self.stdout.write('DRY RUN | bez HTTP-a i upisa.')
            return
        try:
            client = MungosClient()
            if not client._access_code or urlsplit(client._base_url).hostname != 'staging.mungos.ba':
                raise MungosError('NOT_SENT | Potreban je STAGING i access code.')
            with transaction.atomic():
                current = MungosProductMapping.objects.select_for_update().get(pk=mapping.pk)
                if current.last_sync_status in BLOCKED or current.mungos_uuid != mapping.mungos_uuid:
                    self.stdout.write('SKIPPED | Mapping je promijenjen ili blokiran.')
                    return
                current.last_sync_status = 'IN_FLIGHT'
                current.last_sync_error = 'review_required_after_interruption'
                current.save(update_fields=['last_sync_status', 'last_sync_error', 'updated_at'])
            for attempt in range(3):
                if self.last_request is not None:
                    time.sleep(max(0, options['delay'] - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                status, body, truncated = getattr(client, 'sync_' + self.operation)(str(current.mungos_uuid), payload)
                if status != 429:
                    break
                time.sleep(max(options['delay'], retry_seconds(getattr(client, 'retry_after', None))))
        except MungosError as error:
            if str(error).startswith('UNKNOWN_REMOTE_STATE'):
                MungosProductMapping.objects.filter(pk=mapping.pk).update(
                    last_sync_status='UNKNOWN_REMOTE_STATE', last_sync_error='network_or_response_error')
            self.stdout.write(str(error))
            error_class = StopSyncRun if error.http_status in (401, 403) or not str(error).startswith('UNKNOWN_REMOTE_STATE') else CommandError
            raise error_class(str(error)) from None
        success = 200 <= status < 300
        unknown = status >= 500 or 300 <= status < 400
        current.last_sync_status = 'UPDATED' if success else 'UNKNOWN_REMOTE_STATE' if unknown else 'FAILED'
        current.last_sync_error = '' if success else f'api_{status}'
        if success:
            current.last_synced_at = timezone.now()
        current.save(update_fields=['last_sync_status', 'last_sync_error', 'last_synced_at', 'updated_at'])
        if status in (400, 404, 409):
            self.stdout.write(sanitized_json(dict(product_id=product.pk, reason=f'api_{status}',
                                                  api_error=safe_api_error(body), api_error_truncated=truncated)))
        self.stdout.write(f'{current.last_sync_status} | HTTP status: {status} | Nema CREATE fallbacka.')
        if status in (401, 403):
            raise StopSyncRun(f'Auth failure | HTTP status: {status}')
        if not success:
            raise CommandError(f'{current.last_sync_status} | HTTP status: {status}')
