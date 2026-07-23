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
  --out DIR   odredišni folder (default: backups/)
"""
from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


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
        force = options['force']
        include_media = options['media']
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')

        base_dir = Path(settings.BASE_DIR)
        out_dir = Path(options['out']).expanduser() if options['out'] else base_dir / 'backups'
        out_dir.mkdir(parents=True, exist_ok=True)

        engine = settings.DATABASES['default']['ENGINE']
        if 'sqlite' in engine:
            dest = self._backup_sqlite(base_dir, out_dir, stamp, force=force)
        elif 'postgresql' in engine or 'postgres' in engine:
            dest = self._backup_postgres(out_dir, stamp, force=force)
        else:
            raise CommandError(f'Nepodržan DB engine: {engine}')

        self.stdout.write(self.style.SUCCESS(f'✓ Backup uspješan'))
        self.stdout.write(f'  Fajl:     {dest}')
        self.stdout.write(f'  Veličina: {dest.stat().st_size / 1024:.1f} KB')
        self.stdout.write(f'  Folder:   {out_dir.resolve()}')

        if include_media:
            media_root = Path(settings.MEDIA_ROOT)
            if not media_root.is_dir():
                self.stdout.write(self.style.WARNING(f'  media/ ne postoji: {media_root}'))
            else:
                media_dest = out_dir / f'media-{stamp}.tar.gz'
                if media_dest.exists() and not force:
                    raise CommandError(f'Već postoji {media_dest} — koristi --force')
                archive_base = out_dir / f'media-{stamp}'
                shutil.make_archive(str(archive_base), 'gztar', root_dir=str(media_root))
                made = Path(str(archive_base) + '.tar.gz')
                self.stdout.write(self.style.SUCCESS(f'✓ Backup media: {made}'))

    def _backup_sqlite(self, base_dir: Path, out_dir: Path, stamp: str, *, force: bool) -> Path:
        db_name = settings.DATABASES['default'].get('NAME')
        src = Path(db_name) if db_name else base_dir / 'db.sqlite3'
        if not src.is_file():
            raise CommandError(f'SQLite fajl nije pronađen: {src}')

        dest = out_dir / f'db-{stamp}.sqlite3'
        if dest.exists() and not force:
            raise CommandError(f'Već postoji {dest} — koristi --force ili sačekaj sekundu')

        # Prefer sqlite3 .backup (sigurno dok app radi)
        try:
            import sqlite3

            src_conn = sqlite3.connect(str(src))
            try:
                dst_conn = sqlite3.connect(str(dest))
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f'sqlite backup API nije uspio ({exc}); radim običan copy'))
            if dest.exists():
                dest.unlink()
            shutil.copy2(src, dest)

        return dest

    def _backup_postgres(self, out_dir: Path, stamp: str, *, force: bool) -> Path:
        dest = out_dir / f'postgres-{stamp}.dump'
        if dest.exists() and not force:
            raise CommandError(f'Već postoji {dest} — koristi --force')

        database_url = None
        # dj_database_url stavlja pojedinačna polja; prefer DATABASE_URL iz env-a
        import os

        database_url = os.environ.get('DATABASE_URL', '').strip()
        if not database_url:
            db = settings.DATABASES['default']
            user = db.get('USER') or ''
            password = db.get('PASSWORD') or ''
            host = db.get('HOST') or 'localhost'
            port = db.get('PORT') or '5432'
            name = db.get('NAME') or ''
            if password:
                database_url = f'postgres://{user}:{password}@{host}:{port}/{name}'
            else:
                database_url = f'postgres://{user}@{host}:{port}/{name}'

        pg_dump = shutil.which('pg_dump')
        if not pg_dump:
            raise CommandError(
                'pg_dump nije instaliran. Na macOS: brew install libpq && brew link --force libpq\n'
                'Ili u Render Dashboard → Postgres → Backups / External Connection + pg_dump.'
            )

        cmd = [
            pg_dump,
            database_url,
            '-Fc',
            '-f',
            str(dest),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            if dest.exists():
                dest.unlink(missing_ok=True)
            raise CommandError(
                f'pg_dump nije uspio (exit {exc.returncode}).\n'
                f'{exc.stderr or exc.stdout or ""}'
            ) from exc

        return dest
