import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from bera_licensing.services import sync_license
from bera_licensing.security import LicenseError


class Command(BaseCommand):
    help = 'Sinhronizacija BERA licence; --watch za zasebni Render background worker.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--watch', action='store_true')

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            try:
                changed = sync_license(force=options['force'])
                self.stdout.write('Licenca osvježena.' if changed else 'Sinhronizacija još nije potrebna ili je u toku.')
            except LicenseError as exc:
                if not options['watch']:
                    raise CommandError(str(exc)) from None
                self.stderr.write(str(exc))
            if not options['watch']:
                return
            time.sleep(settings.BERA_LICENSE_REFRESH_INTERVAL)
