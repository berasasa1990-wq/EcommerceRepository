from django.core.management.base import BaseCommand
from EcommerceApp.models import SiteSettings
from EcommerceApp.utils.images import prepare_homepage_banner


class Command(BaseCommand):
    help = 'Optimizuje postojeće početne slike. Originali ostaju u storageu; bez --apply samo prikazuje plan.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        site = SiteSettings.load()
        for field, size in (('hero_slika', (1600, 600)), ('hero_slika_mobilna', (1080, 1350))):
            image = getattr(site, field)
            if not image:
                continue
            with image.storage.open(image.name, 'rb') as source:
                original = source.read()
                source.seek(0)
                processed = prepare_homepage_banner(source, size=size, max_bytes=200_000 if field.endswith("mobilna") else 80_000)
            self.stdout.write(f'{field}: {len(original)} → {processed.size} bytes')
            if options['apply'] and processed.read() != original:
                processed.seek(0)
                setattr(site, field, processed)
                site.save(update_fields=[field])
                self.stdout.write(self.style.SUCCESS(f'Sačuvano: {field} (original je zadržan).'))
