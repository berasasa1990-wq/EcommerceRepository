from django.core.management.base import BaseCommand, CommandError

from EcommerceApp.models import Product
from EcommerceApp.mungos_client import MungosClient, MungosError
from EcommerceApp.mungos_product import build_product_preview


class Command(BaseCommand):
    help = 'Ručno slanje jednog proizvoda na Mungos STAGING; zahtijeva --confirm.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('product_id', type=int)
        parser.add_argument('--confirm', action='store_true')

    def handle(self, *args, **options):
        if not options['confirm']:
            self.stdout.write('NOT_SENT | Slanje nije izvršeno. Za jedan POST potreban je --confirm.')
            return
        try:
            product = Product.objects.select_related('kategorija').prefetch_related(
                'varijacije', 'dodatne_slike',
            ).get(pk=options['product_id'])
        except Product.DoesNotExist:
            raise CommandError('NOT_SENT | Proizvod ne postoji.') from None
        preview = build_product_preview(product)
        if preview['status'] != 'READY_FOR_REVIEW' or preview['reviewReasons']:
            raise CommandError('NOT_SENT | Payload zahtijeva pregled; slanje nije izvršeno.')
        try:
            status, response, truncated = MungosClient().send_product(preview['payload'])
        except MungosError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f'HTTP status: {status} | Mungos response: {response}')
        if truncated:
            self.stdout.write('Response je skraćen na 65536 bytes.')
        if status == 429:
            raise CommandError('FAILED | HTTP status: 429 | Rate limit; nema automatskog retryja.')
        if not 200 <= status < 300:
            raise CommandError(f'FAILED | HTTP status: {status} | Nema retryja; provjerite Mungos prije novog POST-a.')
        self.stdout.write('SUCCESS | Jedan proizvod poslan na Mungos STAGING.')
