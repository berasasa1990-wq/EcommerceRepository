import signal
import threading

from django.core.management.base import BaseCommand

from EcommerceApp.company_payments_worker import run_worker


class Command(BaseCommand):
    help = 'Provjerava nove uplate na račun Carpologije svakih 10 minuta, izvan web zahtjeva.'

    def handle(self, *args, **options):
        stop = threading.Event()
        def shutdown(signum, frame):
            stop.set()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, shutdown)
        self.stdout.write('Pozadinska provjera uplata: svakih 10 minuta.')
        run_worker(stop)
