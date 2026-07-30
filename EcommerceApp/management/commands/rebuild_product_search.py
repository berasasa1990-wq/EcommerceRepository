from django.core.management.base import BaseCommand

from EcommerceApp.product_search import is_postgres, rebuild_all_product_search_indexes


class Command(BaseCommand):
    help = 'Reindex PostgreSQL FTS / search_document za sve artikle'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=250,
            help='Broj artikala po batch-u (default 250)',
        )

    def handle(self, *args, **options):
        batch = options['batch_size']
        self.stdout.write(
            f'Rebuilding product search index '
            f'({"PostgreSQL FTS + pg_trgm" if is_postgres() else "search_document only / SQLite"})…'
        )
        count = rebuild_all_product_search_indexes(batch_size=batch)
        self.stdout.write(self.style.SUCCESS(f'Done. Indexed {count} products.'))
