from django.core.management.base import BaseCommand, CommandError

from EcommerceApp.models import Product
from EcommerceApp.mungos_product import build_product_preview, sanitized_json


class Command(BaseCommand):
    help = 'Read-only Mungos single-product JSON preview; ne šalje HTTP zahtjeve.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('product_id', type=int)

    def handle(self, *args, **options):
        try:
            product = Product.objects.select_related('kategorija').prefetch_related(
                'varijacije', 'dodatne_slike',
            ).get(pk=options['product_id'])
        except Product.DoesNotExist:
            raise CommandError('Proizvod ne postoji.') from None
        self.stdout.write(sanitized_json(build_product_preview(product)))
