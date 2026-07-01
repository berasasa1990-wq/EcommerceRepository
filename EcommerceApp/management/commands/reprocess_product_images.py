from django.core.management.base import BaseCommand

from EcommerceApp.models import Product, ProductVariation
from EcommerceApp.utils.images import reprocess_existing_image_file


class Command(BaseCommand):
    help = 'Ponovo obrađuje sve slike artikala i varijacija u AVIF format (max 15KB).'

    def handle(self, *args, **options):
        updated = 0
        skipped = 0
        errors = 0

        for product in Product.objects.exclude(slika='').iterator():
            try:
                processed = reprocess_existing_image_file(product.slika)
                if processed is None:
                    skipped += 1
                    continue
                product.slika.save(processed.name, processed, save=True)
                updated += 1
                self.stdout.write(f'OK artikal: {product.naziv}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA artikal {product.pk}: {exc}')

        for variation in ProductVariation.objects.exclude(slika='').iterator():
            try:
                processed = reprocess_existing_image_file(variation.slika)
                if processed is None:
                    skipped += 1
                    continue
                variation.slika.save(processed.name, processed, save=True)
                updated += 1
                self.stdout.write(f'OK varijacija: {variation}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA varijacija {variation.pk}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {updated} obrađeno, {skipped} preskočeno, {errors} grešaka.',
        ))