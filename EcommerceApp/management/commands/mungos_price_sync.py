from EcommerceApp.mungos_partial_command import PartialSyncCommand


class Command(PartialSyncCommand):
    help = 'Mungos product PUT s regularnom i prodajnom cijenom za potvrđeni UUID; default dry-run, --confirm šalje jednom.'
    operation = 'price'
