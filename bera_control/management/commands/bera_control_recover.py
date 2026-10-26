from getpass import getpass
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError, PermissionDenied
from bera_control.services import recover


class Command(BaseCommand):
    help = 'Server-only password recovery. Revokes all BERA Control sessions.'

    def handle(self, *args, **options):
        if input('Za oporavak pristupa upišite BERA CONTROL: ').strip() != 'BERA CONTROL':
            raise CommandError('Oporavak otkazan.')
        password = getpass('Nova lozinka: ')
        if password != getpass('Potvrdi lozinku: '):
            raise CommandError('Lozinke se ne podudaraju.')
        try:
            recover(password)
        except (ValidationError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS('Lozinka promijenjena; prethodne control sesije su opozvane.'))
