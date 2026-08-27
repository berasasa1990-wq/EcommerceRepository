"""Backup i restore Magacin / Django baze (SQLite lokalno, Postgres na Renderu)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

BACKUP_NAME_RE = re.compile(r'^(db|postgres)-(\d{8})-(\d{6})\.(sqlite3|dump)$')


class BackupError(Exception):
    pass


def backup_root(*, create: bool = True) -> Path:
    custom = getattr(settings, 'MAGACIN_BACKUP_DIR', None)
    if custom:
        root = Path(custom)
    else:
        disk = (os.environ.get('RENDER_DISK_PATH') or '').strip()
        root = Path(disk) / 'db-backups' if disk else Path(settings.BASE_DIR) / 'backups'
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def engine_kind() -> str:
    engine = (settings.DATABASES.get('default') or {}).get('ENGINE') or ''
    if 'sqlite' in engine:
        return 'sqlite'
    if 'postgres' in engine:
        return 'postgres'
    raise BackupError(f'Nepodržan DB engine: {engine}')


def size_label(nbytes: int) -> str:
    n = max(0, int(nbytes or 0))
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    return f'{n / (1024 * 1024):.1f} MB'


def _parse_created(name: str, path: Path | None = None):
    match = BACKUP_NAME_RE.match(name or '')
    if match:
        try:
            naive = datetime.strptime(match.group(2) + match.group(3), '%Y%m%d%H%M%S')
            tz = timezone.get_current_timezone()
            if timezone.is_naive(naive):
                return timezone.make_aware(naive, tz)
            return naive
        except (TypeError, ValueError):
            pass
    if path is not None and path.is_file():
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone())
    return None


def _stamp() -> str:
    return timezone.localtime().strftime('%Y%m%d-%H%M%S')


def _sqlite_conn():
    from django.db import connection

    connection.ensure_connection()
    raw = connection.connection
    if raw is None:
        raise BackupError('SQLite veza nije otvorena.')
    return raw


def _database_url() -> str:
    url = (os.environ.get('DATABASE_URL') or '').strip()
    if url:
        return url
    db = settings.DATABASES['default']
    user = db.get('USER') or ''
    password = db.get('PASSWORD') or ''
    host = db.get('HOST') or 'localhost'
    port = db.get('PORT') or '5432'
    name = db.get('NAME') or ''
    if password:
        return f'postgres://{user}:{password}@{host}:{port}/{name}'
    return f'postgres://{user}@{host}:{port}/{name}'


def _sqlite_file_path() -> Path | None:
    name = (settings.DATABASES.get('default') or {}).get('NAME')
    if not name:
        return None
    path = Path(str(name))
    try:
        if path.is_file():
            return path.resolve()
    except OSError:
        return None
    return None


def _backup_sqlite(dest: Path) -> Path:
    import sqlite3

    dest.parent.mkdir(parents=True, exist_ok=True)
    live = _sqlite_file_path()
    dst = sqlite3.connect(str(dest))
    try:
        if live is not None:
            src = sqlite3.connect(f'file:{live}?mode=ro', uri=True)
            try:
                src.backup(dst)
            finally:
                src.close()
        else:
            src = _sqlite_conn()
            dst.executescript('\n'.join(src.iterdump()))
    finally:
        dst.close()
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise BackupError('Backup fajl je prazan.')
    return dest


def _backup_postgres(dest: Path) -> Path:
    pg_dump = shutil.which('pg_dump')
    if not pg_dump:
        raise BackupError(
            'pg_dump nije instaliran. Na macOS: brew install libpq && brew link --force libpq.'
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [pg_dump, _database_url(), '-Fc', '-f', str(dest)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise BackupError(
            f'pg_dump nije uspio (exit {exc.returncode}). {exc.stderr or exc.stdout or ""}'.strip()
        ) from exc
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise BackupError('Backup fajl je prazan.')
    return dest


def _restore_sqlite(src_path: Path) -> None:
    import sqlite3
    from django.db import connections

    live = _sqlite_file_path()
    if live is not None:
        connections.close_all()
        for suffix in ('-wal', '-shm'):
            extra = Path(str(live) + suffix)
            extra.unlink(missing_ok=True)
        shutil.copy2(src_path, live)
        for suffix in ('-wal', '-shm'):
            extra = Path(str(live) + suffix)
            extra.unlink(missing_ok=True)
        return

    src = sqlite3.connect(f'file:{src_path}?mode=ro', uri=True)
    try:
        dst = _sqlite_conn()
        src.backup(dst)
        dst.commit()
    finally:
        src.close()


def _restore_postgres(src_path: Path) -> None:
    from django.db import connections

    pg_restore = shutil.which('pg_restore')
    if not pg_restore:
        raise BackupError(
            'pg_restore nije instaliran. Na macOS: brew install libpq && brew link --force libpq.'
        )
    connections.close_all()
    cmd = [
        pg_restore,
        '--clean',
        '--if-exists',
        '--no-owner',
        '--no-acl',
        '--dbname',
        _database_url(),
        str(src_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise BackupError(
            f'pg_restore nije uspio (exit {exc.returncode}). {exc.stderr or exc.stdout or ""}'.strip()
        ) from exc


def _info(path: Path) -> dict:
    created = _parse_created(path.name, path)
    return {
        'name': path.name,
        'path': path,
        'size': path.stat().st_size if path.is_file() else 0,
        'size_label': size_label(path.stat().st_size if path.is_file() else 0),
        'created_at': created,
        'kind': 'sqlite' if path.suffix == '.sqlite3' else 'postgres',
    }


def create_backup(*, out_dir: Path | str | None = None, keep=None, protect=None) -> dict:
    root = Path(out_dir).expanduser().resolve() if out_dir else backup_root()
    root.mkdir(parents=True, exist_ok=True)
    kind = engine_kind()
    stamp = _stamp()
    dest = root / (f'db-{stamp}.sqlite3' if kind == 'sqlite' else f'postgres-{stamp}.dump')
    if dest.exists():
        stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
        dest = root / (f'db-{stamp}.sqlite3' if kind == 'sqlite' else f'postgres-{stamp}.dump')
    if dest.exists():
        raise BackupError('Backup fajl već postoji — pokušaj ponovo.')
    if kind == 'sqlite':
        _backup_sqlite(dest)
    else:
        _backup_postgres(dest)
    return _info(dest)


def list_backups(*, out_dir: Path | str | None = None) -> list[dict]:
    try:
        root = Path(out_dir).expanduser().resolve() if out_dir else backup_root(create=False)
    except (OSError, BackupError):
        return []
    if not root.is_dir():
        return []
    rows = [_info(p) for p in root.iterdir() if p.is_file() and BACKUP_NAME_RE.match(p.name)]
    rows.sort(key=lambda row: row['name'], reverse=True)
    return rows


def last_backup(*, out_dir: Path | str | None = None) -> dict | None:
    try:
        rows = list_backups(out_dir=out_dir)
    except (OSError, BackupError):
        return None
    return rows[0] if rows else None


def resolve_backup_file(name: str, *, out_dir: Path | str | None = None) -> Path:
    raw = (name or '').strip()
    if not raw or raw != Path(raw).name or not BACKUP_NAME_RE.match(raw):
        raise BackupError('Nepoznat backup fajl.')
    root = Path(out_dir).expanduser().resolve() if out_dir else backup_root(create=False)
    path = (root / raw).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise BackupError('Backup fajl nije pronađen.')
    return path


def restore_backup(name: str, *, out_dir: Path | str | None = None, safety: bool = True) -> dict:
    src = resolve_backup_file(name, out_dir=out_dir)
    kind = engine_kind()
    src_kind = 'sqlite' if src.suffix == '.sqlite3' else 'postgres'
    if src_kind != kind:
        raise BackupError(
            f'Ovaj backup je za {src_kind}, a trenutna baza je {kind}.'
        )
    safety_info = None
    if safety:
        safety_info = create_backup(out_dir=out_dir, protect={src.name})
    if kind == 'sqlite':
        _restore_sqlite(src)
    else:
        _restore_postgres(src)
    return {
        'restored': src.name,
        'safety': safety_info['name'] if safety_info else '',
    }
