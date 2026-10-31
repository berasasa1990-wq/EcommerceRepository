from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from EcommerceApp.models import SiteSettings
from EcommerceApp.utils.images import PROMO_BANNER_MAX_BYTES, prepare_promo_banner


class Command(BaseCommand):
    help = 'Optimise existing left/right promo images to 60 KB; keep original files.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Save optimised files and update banner references.')

    def handle(self, *args, **options):
        site = SiteSettings.load()
        prepared = []
        for name in ('promo_new_slika', 'promo_new_slika_mobilna', 'promo_sale_slika', 'promo_sale_slika_mobilna'):
            field = getattr(site, name)
            if not field:
                continue
            try:
                with field.open('rb') as source:
                    raw = source.read()
                if len(raw) <= PROMO_BANNER_MAX_BYTES:
                    self.stdout.write(f'{name}: {len(raw)} bytes — već ispod 60 KB')
                    continue
                output = prepare_promo_banner(ContentFile(raw, name=field.name), mobile=name.endswith('_mobilna'))
            except Exception as exc:
                raise CommandError(f'{name}: {exc}') from exc
            prepared.append((name, output))
            self.stdout.write(f'{name}: {len(raw)} → {output.size} bytes')
        if options['apply'] and prepared:
            for name, output in prepared:
                path = Path(output.name)
                filename = f'{path.stem}-60kb-{uuid4().hex[:8]}{path.suffix}'
                getattr(site, name).save(filename, output, save=False)
            site.save(update_fields=[name for name, _ in prepared])
            self.stdout.write(self.style.SUCCESS('Banneri su optimizovani. Originalne datoteke su sačuvane.'))
        elif prepared:
            self.stdout.write('Pregled bez izmjena. Dodaj --apply za čuvanje.')
