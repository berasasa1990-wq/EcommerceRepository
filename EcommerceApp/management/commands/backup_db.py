"""
Ručni backup baze.

Lokalno (SQLite):
  python manage.py backup_db
  python manage.py backup_db --force

Render / Postgres (ako je DATABASE_URL postavljen):
  python manage.py backup_db
  # koristi pg_dump ako je dostupan, inače jasna greška

Opcije:
  --force     prepiši ako fajl već postoji (isti timestamp je rijedak)
  --media     uz bazu spakuj i media/ folder
  --out DIR   odredišni folder (default: backups/ ili RENDER_DISK_PATH/db-backups)
"""
from __future__ import annotations

import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from EcommerceApp.db_backup import BackupError, create_backup


class Command(BaseCommand):
    help = 'Napravi backup baze (SQLite lokalno ili Postgres preko pg_dump).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Prepiši postojeći backup fajl istog imena.',
        )
        parser.add_argument(
            '--media',
            action='store_true',
            help='Dodatno spakuj media/ u .tar.gz pored baze.',
        )
        parser.add_argument(
            '--out',
            type=str,
            default='',
            help='Folder za backup (default: <project>/backups).',
        )

    def handle(self, *args, **options):
        include_media = options['media']
        out_dir = Path(options['out']).expanduser() if options['out'] else None

        try:
            info = create_backup(out_dir=out_dir)
        except BackupError as exc:
            raise CommandError(str(exc)) from exc

        dest = info['path']
        self.stdout.write(self.style.SUCCESS('✓ Backup uspješan'))
        self.stdout.write(f'  Fajl:     {dest}')
        self.stdout.write(f'  Veličina: {info["size_label"]}')
        self.stdout.write(f'  Folder:   {dest.parent}')

        if include_media:
            media_root = Path(settings.MEDIA_ROOT)
            if not media_root.is_dir():
                self.stdout.write(self.style.WARNING(f'  media/ ne postoji: {media_root}'))
                return
            stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
            folder = dest.parent
            archive_base = folder / f'media-{stamp}'
            shutil.make_archive(str(archive_base), 'gztar', root_dir=str(media_root))
            made = Path(str(archive_base) + '.tar.gz')
            self.stdout.write(self.style.SUCCESS(f'✓ Backup media: {made}'))
