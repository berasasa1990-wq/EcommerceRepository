from django.core.management.base import BaseCommand

from EcommerceApp.models import Product, ProductVariation
from EcommerceApp.utils.images import reprocess_existing_image_file, save_processed_product_image


class Command(BaseCommand):
    help = 'Ponovo obrađuje slike artikala u AVIF (max 15KB) + responsive varijante 120w/200w.'

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
                save_processed_product_image(product.slika, processed)
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
                save_processed_product_image(variation.slika, processed)
                variation.save(update_fields=['slika'])
                updated += 1
                self.stdout.write(f'OK varijacija: {variation}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA varijacija {variation.pk}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {updated} obrađeno, {skipped} preskočeno, {errors} grešaka.',
        ))