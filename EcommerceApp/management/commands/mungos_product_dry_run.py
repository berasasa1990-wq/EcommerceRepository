from django.core.management.base import BaseCommand, CommandError

from EcommerceApp.models import Product
from EcommerceApp.mungos_product import build_product_preview, sanitized_json
from EcommerceApp.mungos_update import build_mungos_update_payload
from EcommerceApp.mungos_client import validate_product_uuid, MungosError


class Command(BaseCommand):
    help = 'Read-only Mungos single-product JSON preview; ne šalje HTTP zahtjeve.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('product_id', type=int)
        parser.add_argument('--operation', choices=('create', 'update'), default='create')
        parser.add_argument('--mungos-uuid', help='Opcionalni UUID za lokalnu provjeru UPDATE putanje.')

    def handle(self, *args, **options):
        if options.get('mungos_uuid'):
            if options['operation'] != 'update':
                raise CommandError('--mungos-uuid se koristi samo za UPDATE pregled.')
            try:
                validate_product_uuid(options['mungos_uuid'])
            except MungosError as error:
                raise CommandError(str(error)) from None
        try:
            product = Product.objects.select_related('kategorija').prefetch_related(
                'varijacije', 'dodatne_slike',
            ).get(pk=options['product_id'])
        except Product.DoesNotExist:
            raise CommandError('Proizvod ne postoji.') from None
        preview = build_product_preview(product)
        if options['operation'] == 'update':
            preview = build_mungos_update_payload(preview)
        self.stdout.write(sanitized_json(preview))
