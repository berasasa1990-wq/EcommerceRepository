"""
Kopira postojeće lokalne / Render media fajlove na Cloudflare R2.

Koristi se jednom nakon što podesiš R2_* env varijable.

  python manage.py sync_media_to_r2
  python manage.py sync_media_to_r2 --dry-run
  python manage.py sync_media_to_r2 --prefix products/
"""
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Upload lokalnih media fajlova na Cloudflare R2 (default storage).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Samo ispiši šta bi se uploadalo.',
        )
        parser.add_argument(
            '--prefix',
            type=str,
            default='',
            help='Samo podfolder, npr. products/ ili banners/',
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            default=True,
            help='Preskoči ako fajl već postoji na R2 (default: da).',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Uploadaj i ako fajl već postoji (overwrite).',
        )

    def handle(self, *args, **options):
        if not getattr(settings, 'USE_R2_MEDIA', False):
            self.stderr.write(
                self.style.ERROR(
                    'R2 nije uključen. Postavi R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, '
                    'R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME (i R2_CUSTOM_DOMAIN).',
                ),
            )
            return

        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists():
            self.stderr.write(self.style.ERROR(f'MEDIA_ROOT ne postoji: {media_root}'))
            return

        prefix = (options.get('prefix') or '').lstrip('/')
        root = media_root / prefix if prefix else media_root
        if not root.exists():
            self.stderr.write(self.style.ERROR(f'Folder ne postoji: {root}'))
            return

        dry = options['dry_run']
        overwrite = options['overwrite']
        skip_existing = options['skip_existing'] and not overwrite

        uploaded = 0
        skipped = 0
        errors = 0

        self.stdout.write(f'Izvor: {media_root}')
        self.stdout.write(f'Cilj storage: {default_storage.__class__.__name__}')
        self.stdout.write(f'MEDIA_URL: {settings.MEDIA_URL}')

        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(media_root).as_posix()
            try:
                if skip_existing and default_storage.exists(rel):
                    skipped += 1
                    continue
                if dry:
                    self.stdout.write(f'[dry-run] {rel}')
                    uploaded += 1
                    continue
                data = path.read_bytes()
                if default_storage.exists(rel):
                    default_storage.delete(rel)
                # Sačuvaj pod istim relativnim putem (products/…, banners/…)
                saved = default_storage.save(rel, ContentFile(data, name=path.name))
                uploaded += 1
                if uploaded <= 30 or uploaded % 50 == 0:
                    self.stdout.write(f'OK {saved}')
            except Exception as exc:
                errors += 1
                self.stderr.write(f'GREŠKA {rel}: {exc}')

        self.stdout.write(self.style.SUCCESS(
            f'Završeno: {uploaded} uploadano, {skipped} preskočeno, {errors} grešaka'
            + (' (dry-run)' if dry else '')
            + '.',
        ))
