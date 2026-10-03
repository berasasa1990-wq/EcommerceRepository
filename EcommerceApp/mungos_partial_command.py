"""Manual partial sync; only integration metadata may be persisted."""
from contextlib import nullcontext
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from .models import Product, MungosProductMapping
from .mungos_bulk_lock import bulk_lock
from .mungos_client import MungosClient, MungosError
from .mungos_partial import build_partial_payload
from .mungos_product import build_product_preview, sanitized_json

BLOCKED = {'IN_FLIGHT', 'UNKNOWN_REMOTE_STATE', 'UNKNOWN'}


class PartialSyncCommand(BaseCommand):
    requires_system_checks = []
    operation = None

    def add_arguments(self, parser):
        parser.add_argument('--product-id', required=True, type=int)
        parser.add_argument('--confirm', action='store_true')

    def handle(self, *args, **options):
        with bulk_lock() if options['confirm'] else nullcontext():
            self.sync(options)

    def sync(self, options):
        try:
            product = Product.objects.select_related('kategorija', 'mungos_mapping').prefetch_related(
                'varijacije', 'dodatne_slike').get(pk=options['product_id'])
        except Product.DoesNotExist:
            raise CommandError('NOT_SENT | Proizvod ne postoji.') from None
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
            status, _body, _truncated = getattr(client, 'sync_' + self.operation)(str(current.mungos_uuid), payload)
        except MungosError as error:
            if str(error).startswith('UNKNOWN_REMOTE_STATE'):
                MungosProductMapping.objects.filter(pk=mapping.pk).update(
                    last_sync_status='UNKNOWN_REMOTE_STATE', last_sync_error='network_or_response_error')
            raise CommandError(str(error)) from None
        success = 200 <= status < 300
        unknown = status >= 500 or 300 <= status < 400
        current.last_sync_status = 'UPDATED' if success else 'UNKNOWN_REMOTE_STATE' if unknown else 'FAILED'
        current.last_sync_error = '' if success else f'api_{status}'
        if success:
            current.last_synced_at = timezone.now()
        current.save(update_fields=['last_sync_status', 'last_sync_error', 'last_synced_at', 'updated_at'])
        self.stdout.write(f'{current.last_sync_status} | HTTP status: {status} | Nema retryja ni CREATE fallbacka.')
        if not success:
            raise CommandError(f'{current.last_sync_status} | HTTP status: {status}')
