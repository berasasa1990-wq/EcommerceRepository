"""SELECT-only inspection of durable uncertain Mungos attempts."""
from django.core.management.base import BaseCommand
from django.db.models import Q

from EcommerceApp.models import MungosProductMapping
from EcommerceApp.mungos_diagnostics import safe_api_error
from EcommerceApp.mungos_product import sanitized_json


class Command(BaseCommand):
    help = 'Read-only Mungos unknown state audit; bez HTTP-a i promjene baze.'
    requires_system_checks = []

    def handle(self, *args, **options):
        mappings = MungosProductMapping.objects.filter(
            Q(last_sync_status__in=('IN_FLIGHT', 'UNKNOWN_REMOTE_STATE')) | Q(mungos_uuid__isnull=True)
        ).select_related('product').order_by('product_id')
        total = 0
        for mapping in mappings.iterator(chunk_size=200):
            total += 1
            self.stdout.write(sanitized_json({
                'product_id': mapping.product_id, 'sku': mapping.product.sifra or '',
                'name': mapping.product.naziv, 'mapping_id': mapping.pk,
                'status': 'UNKNOWN_REMOTE_STATE', 'mungos_uuid': str(mapping.mungos_uuid) if mapping.mungos_uuid else None,
                'last_sync_status': mapping.last_sync_status,
                'last_sync_error': safe_api_error(mapping.last_sync_error),
                'created_at': mapping.created_at.isoformat(), 'updated_at': mapping.updated_at.isoformat(),
                'last_synced_at': mapping.last_synced_at.isoformat() if mapping.last_synced_at else None,
            }))
        self.stdout.write(sanitized_json({'UNKNOWN_REMOTE_STATE': total}))
