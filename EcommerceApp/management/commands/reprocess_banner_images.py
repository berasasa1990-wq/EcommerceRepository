from django.core.management.base import BaseCommand

from EcommerceApp.models import Banner
from EcommerceApp.utils.images import reprocess_existing_banner_file


class Command(BaseCommand):
    help = 'Ponovo obrađuje sve banner slike u AVIF format uz viši kvalitet.'

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
                banner.slika.save(processed.name, processed, save=True)
                updated += 1
                self.stdout.write(f'OK banner: {banner}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA banner {banner.pk}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {updated} obrađeno, {skipped} preskočeno, {errors} grešaka.',
        ))