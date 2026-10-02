from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from EcommerceApp.models import Product, MungosProductMapping
from EcommerceApp.mungos_client import validate_product_uuid, MungosError


class Command(BaseCommand):
    help = 'Ručno registruje provjeren Mungos UUID bez HTTP-a; ne prepisuje drugi UUID.'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('product_id', type=int)
        parser.add_argument('mungos_uuid')

    def handle(self, *args, **options):
        try:
            uuid = validate_product_uuid(options['mungos_uuid'])
            with transaction.atomic():
                product = Product.objects.get(pk=options['product_id'])
                mapping, _ = MungosProductMapping.objects.get_or_create(product=product)
                mapping = MungosProductMapping.objects.select_for_update().get(pk=mapping.pk)
                if mapping.mungos_uuid and str(mapping.mungos_uuid) != uuid.lower():
                    raise CommandError('Postojeći UUID se ne prepisuje.')
                mapping.mungos_uuid = uuid
                mapping.sku_snapshot = product.sifra or ''
                mapping.last_sync_status = 'REGISTERED'
                mapping.last_sync_error = ''
                mapping.save()
        except Product.DoesNotExist:
            raise CommandError('Proizvod ne postoji.') from None
        except (MungosError, IntegrityError):
            raise CommandError('Neispravan UUID ili UUID već pripada drugom proizvodu.') from None
        self.stdout.write(f'REGISTERED | Product ID: {product.pk} | UUID: {uuid} | bez HTTP-a')
