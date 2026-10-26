from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import PermissionDenied
from bera_control.services import issue_bootstrap


class Command(BaseCommand):
    help = 'Generate a single-use BERA Control bootstrap token valid for 10 minutes.'

    def handle(self, *args, **options):
        try:
            token = issue_bootstrap()
        except PermissionDenied as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write('Otvorite /bera-control/setup/ i unesite token (vrijedi 10 minuta):')
        self.stdout.write(token)
