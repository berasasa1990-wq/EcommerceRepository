from django.core.management.base import BaseCommand, CommandError

from EcommerceApp.mungos_client import MungosClient, MungosError


class Command(BaseCommand):
    help = 'GET /Liveness/check/hello sa X-Api-Key i konfiguracijom izabranog okruženja; ispisuje samo status.'
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            status = MungosClient().liveness()
        except MungosError as exc:
            raise CommandError(f'FAILED | HTTP status: N/A | {exc}') from None
        if not 200 <= status < 300:
            raise CommandError(f'FAILED | HTTP status: {status}')
        self.stdout.write(self.style.SUCCESS(f'SUCCESS | HTTP status: {status}'))
