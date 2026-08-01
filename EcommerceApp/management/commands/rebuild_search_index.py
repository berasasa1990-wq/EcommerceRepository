"""Backfill Product search_document / normalized fields."""

from django.core.management.base import BaseCommand

from EcommerceApp.models import Product, ProductVariation
from EcommerceApp.search.normalize import normalize_search_text


class Command(BaseCommand):
    help = 'Rebuild denormalized product search fields (naziv_normalized, search_document, …)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch',
            type=int,
            default=200,
            help='Batch size (default 200)',
        )

    def handle(self, *args, **options):
        batch = options['batch']
        total = Product.objects.count()
        self.stdout.write(f'Rebuilding search fields for {total} products…')

        # Variations first
        v_updated = 0
        for var in ProductVariation.objects.iterator(chunk_size=batch):
            ProductVariation.objects.filter(pk=var.pk).update(
                naziv_normalized=normalize_search_text(var.naziv or '')[:120],
                sifra_normalized=normalize_search_text(var.sifra or '')[:80],
            )
            v_updated += 1
        self.stdout.write(f'  variations: {v_updated}')

        done = 0
        qs = Product.objects.all().select_related(
            'brend', 'kategorija', 'kategorija__roditelj',
        ).prefetch_related('tagovi', 'varijacije')
        for product in qs.iterator(chunk_size=batch):
            product.rebuild_search_document(save=True)
            done += 1
            if done % batch == 0:
                self.stdout.write(f'  products: {done}/{total}')
        self.stdout.write(self.style.SUCCESS(f'Done. {done} products, {v_updated} variations.'))
