"""Local-only audit. Does not read credentials or contact Odoo."""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from EcommerceApp.warehouse_audit import warehouse_snapshot


class Command(BaseCommand):
    help = 'READ ONLY: fingerprint local warehouse/products/orders/reservations and existing availability.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--compare', metavar='BEFORE_JSON', help='Compare with a previously redirected audit JSON. Nonzero exit for any difference.')

    def handle(self, *args, **options):
        try:
            report = warehouse_snapshot()
        except Exception as exc:
            raise CommandError(f'Local read-only audit failed ({type(exc).__name__}). No successful fingerprint produced.') from exc
        identical = None
        if options['compare']:
            try:
                before = json.loads(Path(options['compare']).read_text())
            except (OSError, ValueError):
                raise CommandError('Cannot read valid BEFORE_JSON.') from None
            if before.get('schema') != report['schema']:
                raise CommandError('Fingerprint schemas differ; comparison is invalid.')
            identical = before.get('fingerprint') == report['fingerprint']
            report['comparison'] = {
                'identical': identical,
                'before_fingerprint': before.get('fingerprint'),
                'changed_tables': sorted(key for key in set(before.get('tables', {})) | set(report['tables'])
                                        if before.get('tables', {}).get(key) != report['tables'].get(key)),
                'availability_identical': before.get('warehouse', {}).get('availability') == report['warehouse']['availability'],
            }
        self.stdout.write(json.dumps(report, cls=DjangoJSONEncoder, sort_keys=True, indent=2, ensure_ascii=False))
        if identical is False:
            raise CommandError('Local state differs: permitted difference = 0. Review comparison; no data was changed by this command.')
