from django.core.management.base import BaseCommand

from EcommerceApp.models import Product, ProductVariation
from EcommerceApp.utils.images import (
    PRODUCT_RESPONSIVE_WIDTHS,
    reprocess_existing_image_file,
    save_processed_image,
)


class Command(BaseCommand):
    help = 'Ponovo obrađuje slike artikala u AVIF (max 15KB) + responsive 120/200/320w.'

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
                save_processed_image(product.slika, processed, responsive_widths=PRODUCT_RESPONSIVE_WIDTHS)
                product.save(update_fields=['slika'])
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
                save_processed_image(variation.slika, processed, responsive_widths=PRODUCT_RESPONSIVE_WIDTHS)
                variation.save(update_fields=['slika'])
                updated += 1
                self.stdout.write(f'OK varijacija: {variation}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA varijacija {variation.pk}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {updated} obrađeno, {skipped} preskočeno, {errors} grešaka.',
        ))