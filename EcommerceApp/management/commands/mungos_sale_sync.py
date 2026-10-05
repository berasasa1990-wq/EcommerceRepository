"""Sale-only manual sync, reusing the durable bulk CREATE/full UPDATE flow."""
from collections import Counter
from contextlib import nullcontext

from django.conf import settings
from django.core.management.base import CommandError

from EcommerceApp.models import Product
from EcommerceApp.mungos_bulk_lock import bulk_lock
from EcommerceApp.mungos_client import MungosClient, MungosError, configured_endpoint
from EcommerceApp.mungos_product import build_product_preview, sanitized_json
from .mungos_bulk_sync import Command as BulkCommand, BLOCKED


class Command(BulkCommand):
    help = 'SALE proizvodi: dry-run; --confirm koristi siguran CREATE/full PUT.'

    def add_arguments(self, parser):
        parser.add_argument('--product-id', type=int)
        parser.add_argument('--limit', type=int)
        parser.add_argument('--confirm', action='store_true')

    def format_product_row(self, product, row):
        prices = product._mungos_sale_prices
        regular, selling = prices['Price'], prices['SellingPrice']
        return dict(PRODUCT_ID=product.pk, SKU=product.sifra, NAME=product.naziv,
                    REGULAR_PRICE=regular, SELLING_PRICE=selling,
                    DISCOUNT_PERCENT=round((regular - selling) / regular * 100, 2),
                    ACTION=row['result'] if row['result'] in ('CREATED', 'UPDATED') else 'SKIPPED',
                    MUNGOS_UUID=row['mungos_uuid'], RESULT=row['result'], REASON=row['reason'])

    def handle(self, *args, **options):
        if ((options['limit'] is not None and options['limit'] < 1)
                or (options['product_id'] is not None and options['product_id'] < 1)):
            raise CommandError('limit/product-id mora biti pozitivan.')
        try:
            configured_endpoint()
        except MungosError as error:
            raise CommandError(str(error)) from None
        products = Product.objects.order_by('pk').select_related('kategorija', 'mungos_mapping').prefetch_related(
            'varijacije', 'dodatne_slike')
        if options['product_id'] is not None:
            products = products.filter(pk=options['product_id'])
            if not products.exists():
                raise CommandError('NOT_SENT | Proizvod ne postoji.')
        command = self

        class SaleSelection:
            def iterator(self, chunk_size):
                selected = 0
                for product in products.iterator(chunk_size=chunk_size):
                    preview = build_product_preview(product)
                    prices = (preview.get('payload') or {}).get('ProductPrice')
                    if not prices or prices['Price'] is None or prices['SellingPrice'] is None:
                        continue
                    if prices['SellingPrice'] >= prices['Price']:
                        continue
                    product._mungos_sale_prices = prices
                    mapping = getattr(product, 'mungos_mapping', None)
                    if (preview['status'] != 'READY_FOR_REVIEW' or preview['reviewReasons']
                            or (mapping and (mapping.last_sync_status in BLOCKED or not mapping.mungos_uuid))):
                        command.stdout.write(sanitized_json(command.format_product_row(product,
                            dict(result='SKIPPED', mungos_uuid=str(mapping.mungos_uuid) if mapping and mapping.mungos_uuid else None,
                                 reason='unsupported_or_unknown'))))
                        continue
                    yield product
                    selected += 1
                    if options['limit'] is not None and selected >= options['limit']:
                        break

        client = None
        if options['confirm']:
            try:
                client = MungosClient()
                if not client._access_code:
                    raise MungosError('Slanje zahtijeva access code za izabrano okruženje.')
            except MungosError as error:
                raise CommandError(str(error)) from None
        self.stdout.write(f'CONFIRMED {settings.MUNGOS_ENVIRONMENT.upper()}' if client else 'DRY RUN | bez HTTP-a')
        with bulk_lock() if client else nullcontext():
            self.run_products(SaleSelection(), {'delay': 1.0}, client, Counter(), Counter())
