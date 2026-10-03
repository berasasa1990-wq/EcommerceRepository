from EcommerceApp.mungos_partial_command import PartialSyncCommand


class Command(PartialSyncCommand):
    help = 'Mungos quantity PUT za potvrđeni UUID; default dry-run, --confirm šalje jednom.'
    operation = 'quantity'
