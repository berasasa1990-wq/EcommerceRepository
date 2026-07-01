from django.core.management.base import BaseCommand

from EcommerceApp.models import HomeVlog
from EcommerceApp.utils.images import (
    VLOG_RESPONSIVE_WIDTHS,
    reprocess_existing_vlog_file,
    save_processed_image,
)


class Command(BaseCommand):
    help = 'Ponovo obrađuje vlog slike u AVIF (max 18KB) + responsive 180/280/360w.'

    def handle(self, *args, **options):
        updated = 0
        skipped = 0
        errors = 0

        for vlog in HomeVlog.objects.exclude(slika='').iterator():
            try:
                processed = reprocess_existing_vlog_file(vlog.slika)
                if processed is None:
                    skipped += 1
                    continue
                save_processed_image(
                    vlog.slika,
                    processed,
                    responsive_widths=VLOG_RESPONSIVE_WIDTHS,
                )
                vlog.save(update_fields=['slika'])
                updated += 1
                self.stdout.write(f'OK vlog: {vlog.naslov}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA vlog {vlog.pk}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {updated} obrađeno, {skipped} preskočeno, {errors} grešaka.',
        ))