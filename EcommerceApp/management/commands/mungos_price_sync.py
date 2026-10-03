from EcommerceApp.mungos_partial_command import PartialSyncCommand


class Command(PartialSyncCommand):
    help = 'Mungos price PUT za potvrđeni UUID; default dry-run, --confirm šalje jednom.'
    operation = 'price'
