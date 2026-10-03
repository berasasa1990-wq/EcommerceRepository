"""Explicit staging writes with durable guards and no ambiguous-write retries."""
import json
import math
import time
from collections import Counter
from contextlib import nullcontext
from datetime import datetime, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from EcommerceApp.models import Product, MungosProductMapping
from EcommerceApp.mungos_client import MungosClient, MungosError
from EcommerceApp.mungos_product import build_product_preview, sanitized_json
from EcommerceApp.mungos_update import build_mungos_update_payload

from EcommerceApp.mungos_diagnostics import safe_api_error
from EcommerceApp.mungos_bulk_lock import bulk_lock

STAGING_URL = 'https://staging.mungos.ba/api/v1/connector'
BLOCKED = {'IN_FLIGHT', 'UNKNOWN_REMOTE_STATE'}


def retry_seconds(value):
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return max(0, seconds)
    except (TypeError, ValueError):
        pass
    try:
        return max(0, (parsedate_to_datetime(value) - datetime.now(dt_timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return 0


def response_uuid(body, truncated):
    if truncated:
        return None
    try:
        data = json.loads(body)
        value = data.get('productUuid') if isinstance(data, dict) else None
        # Only the confirmed top-level response key; no guessing nested identities.
        if isinstance(value, str) and str(UUID(value)) == value.lower():
            return UUID(value)
    except (TypeError, ValueError, AttributeError):
        pass
    return None


class Command(BaseCommand):
    help = 'Mungos STAGING bulk: default DRY RUN; HTTP samo uz --confirm.'
    requires_system_checks = []

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group()
        scope.add_argument('--limit', type=int)
        scope.add_argument('--all', action='store_true')
        parser.add_argument('--confirm', action='store_true')
        parser.add_argument('--start-after-id', type=int, default=0)
        parser.add_argument('--delay', type=float, default=1.0)

    def handle(self, *args, **options):
        if settings.MUNGOS_BASE_URL != STAGING_URL:
            raise CommandError('STAGING ONLY | MUNGOS_BASE_URL nije tačan staging connector URL.')
        if (not math.isfinite(options['delay']) or options['delay'] < 1
                or options['start_after_id'] < 0 or (options['limit'] is not None and options['limit'] < 1)):
            raise CommandError('Delay mora biti najmanje 1s; limit pozitivan; start-after-id nenegativan.')
        client = None
        if options['confirm']:
            try:
                client = MungosClient()
                if not client._access_code:
                    raise MungosError('STAGING zahtijeva access code.')
            except MungosError as error:
                raise CommandError(str(error)) from None
        counts = Counter({key: 0 for key in (
            'TOTAL', 'READY', 'READY_CREATE', 'READY_UPDATE', 'CREATED', 'UPDATED',
            'NEEDS_REVIEW', 'SKIPPED', 'FAILED', 'UNKNOWN_REMOTE_STATE', 'RATE_LIMITED',
        )})
        reasons = Counter()
        products = Product.objects.filter(pk__gt=options['start_after_id']).order_by('pk').select_related(
            'kategorija', 'mungos_mapping',
        ).prefetch_related('varijacije', 'dodatne_slike')
        if not options['all']:
            products = products[:options['limit'] if options['limit'] is not None else 10]
        self.stdout.write('CONFIRMED STAGING' if client else 'DRY RUN | bez HTTP-a')
        try:
            with bulk_lock() if client else nullcontext():
                self.run_products(products, options, client, counts, reasons)
        finally:
            self.stdout.write(sanitized_json({'summary': dict(counts), 'reasons': dict(reasons)}))

    def run_products(self, products, options, client, counts, reasons):
        last_request = None
        stop = False
        for product in products.iterator(chunk_size=200):
            counts['TOTAL'] += 1
            mapping = getattr(product, 'mungos_mapping', None)
            operation = 'UPDATE' if mapping and mapping.mungos_uuid else 'CREATE'
            status = None
            reason = ''
            http_status = 'N/A'
            api_error = None
            try:
                if not product.aktivan or product.sakriven_do_stanja:
                    status, reason = 'SKIPPED', 'not_publishable'
                elif mapping and (mapping.last_sync_status in BLOCKED or not mapping.mungos_uuid):
                    status, reason = 'NEEDS_REVIEW', 'unknown_remote_state'
                    counts['UNKNOWN_REMOTE_STATE'] += 1
                else:
                    preview = build_product_preview(product)
                    if operation == 'UPDATE':
                        preview = build_mungos_update_payload(preview)
                    if preview['status'] != 'READY_FOR_REVIEW' or preview['reviewReasons']:
                        status = 'NEEDS_REVIEW'
                        if not preview['categoryCode']:
                            reasons['missing_category'] += 1
                        if preview['variantCount']:
                            reasons['variants_not_supported'] += 1
                        reason = 'payload_validation'
                        if any('sku:' in item or 'SKU' in item for item in preview['reviewReasons']):
                            reasons['invalid_sku'] += 1
                        if any('price:' in item for item in preview['reviewReasons']):
                            reasons['invalid_price'] += 1
                    else:
                        counts['READY'] += 1
                        counts['READY_' + operation] += 1
                        status = 'READY_' + operation
                        if client:
                            # Commit the guard before sending. Concurrent runs cannot claim it twice.
                            with transaction.atomic():
                                mapping, _ = MungosProductMapping.objects.get_or_create(product=product)
                                mapping = MungosProductMapping.objects.select_for_update().get(pk=mapping.pk)
                                if mapping.last_sync_status in BLOCKED or (operation == 'CREATE' and mapping.mungos_uuid):
                                    status, reason = 'SKIPPED', 'concurrent_claim'
                                else:
                                    mapping.last_sync_status = 'IN_FLIGHT'
                                    mapping.last_sync_error = 'review_required_after_interruption'
                                    mapping.sku_snapshot = product.sifra or ''
                                    mapping.save()
                            if status != 'SKIPPED':
                                for attempt in range(3):
                                    if last_request is not None:
                                        time.sleep(max(0, options['delay'] - (time.monotonic() - last_request)))
                                    last_request = time.monotonic()
                                    if operation == 'CREATE':
                                        http_status, body, truncated = client.send_product(preview['payload'])
                                    else:
                                        http_status, body, truncated = client.update_product(str(mapping.mungos_uuid), preview['payload'])
                                    if http_status != 429:
                                        break
                                    counts['RATE_LIMITED'] += 1
                                    time.sleep(max(options['delay'], retry_seconds(getattr(client, 'retry_after', None))))
                                    # No confirmed POST rejection contract: never retry a POST.
                                    if operation == 'CREATE':
                                        break
                                if 200 <= http_status < 300:
                                    uuid = response_uuid(body, truncated) if operation == 'CREATE' else mapping.mungos_uuid
                                    if uuid:
                                        mapping.mungos_uuid = uuid
                                        mapping.last_synced_at = timezone.now()
                                        status = 'CREATED' if operation == 'CREATE' else 'UPDATED'
                                    else:
                                        status, reason = 'UNKNOWN_REMOTE_STATE', 'missing_product_uuid'
                                elif (http_status == 429 and operation == 'CREATE') or http_status >= 500 or 300 <= http_status < 400:
                                    status, reason = 'UNKNOWN_REMOTE_STATE', f'api_{http_status}'
                                else:
                                    status, reason = 'FAILED', f'api_{http_status}'
                                if http_status in (400, 404, 409):
                                    api_error = safe_api_error(body)
                                mapping.last_sync_status = status
                                mapping.last_sync_error = reason
                                mapping.save()
                                stop = http_status in (401, 403)
            except MungosError as error:
                http_status = error.http_status or 'N/A'
                stop = error.http_status in (401, 403)
                if str(error).startswith('UNKNOWN_REMOTE_STATE'):
                    status, reason = 'UNKNOWN_REMOTE_STATE', 'network_or_response_error'
                    if mapping:
                        mapping.last_sync_status = status
                        mapping.last_sync_error = reason
                        mapping.save()
                else:
                    stop = True
                    status, reason = 'FAILED', 'global_configuration'
            except Exception:
                # Never expose exception text or response bodies containing credentials.
                # A persisted IN_FLIGHT guard survives even a failed result save.
                status = 'UNKNOWN_REMOTE_STATE' if mapping and mapping.last_sync_status == 'IN_FLIGHT' else 'FAILED'
                reason = 'local_error'
            if status not in ('READY_CREATE', 'READY_UPDATE'):
                counts[status] += 1
            if reason:
                reasons[reason] += 1
            row = dict(product_id=product.pk, sku=product.sifra or '', operation=operation,
                       mungos_uuid=str(mapping.mungos_uuid) if mapping and mapping.mungos_uuid else None,
                       http_status=http_status, result=status, reason=reason)
            if api_error is not None:
                row['api_error'] = api_error
                row['api_error_truncated'] = truncated
            self.stdout.write(sanitized_json(row))
            if stop:
                break
        if stop:
            raise CommandError('Bulk zaustavljen: auth/global configuration failure; pogledajte report.')
