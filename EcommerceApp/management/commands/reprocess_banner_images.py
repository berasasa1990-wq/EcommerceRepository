from django.core.management.base import BaseCommand

from EcommerceApp.models import Banner
from EcommerceApp.utils.images import (
    BANNER_GRID_RESPONSIVE_WIDTHS,
    reprocess_existing_banner_file,
    save_processed_image,
)


class Command(BaseCommand):
    help = 'Ponovo obrađuje banner slike (Hero JPEG, ostalo AVIF/JPEG).'

    def handle(self, *args, **options):
        updated = 0
        skipped = 0
        errors = 0

        for banner in Banner.objects.exclude(slika='').iterator():
            try:
                processed = reprocess_existing_banner_file(banner.slika, tip=banner.tip)
                if processed is None:
                    skipped += 1
                    continue
                if isinstance(processed, dict) and 'main' in processed:
                    save_processed_image(
                        banner.slika,
                        processed,
                        responsive_widths=(
                            BANNER_GRID_RESPONSIVE_WIDTHS if banner.tip == 'grid' else ()
                        ),
                    )
                    banner.save(update_fields=['slika'])
                else:
                    banner.slika.save(processed.name, processed, save=True)
                updated += 1
                self.stdout.write(f'OK banner: {banner}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA banner {banner.pk}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {updated} obrađeno, {skipped} preskočeno, {errors} grešaka.',
        ))