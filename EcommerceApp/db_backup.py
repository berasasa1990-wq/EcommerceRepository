"""Backup i restore Magacin / Django baze (SQLite lokalno, Postgres na Renderu)."""

from __future__ import annotations

import os
import hashlib
import uuid
import re
import shutil
import subprocess
from contextlib import closing
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

BACKUP_NAME_RE = re.compile(r'^(db|postgres)-(\d{8})-(\d{6})\.(sqlite3|dump)$')
BACKUP_FILE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*\.(sqlite3|dump)$')


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


def backup_search_dirs() -> list[Path]:
    """Svi folderi u kojima mogu stajati backupi — ništa se ne briše, prikazuju se svi."""
    seen = set()
    dirs = []

    def _add(raw):
        if not raw:
            return
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        dirs.append(path)

    custom = getattr(settings, 'MAGACIN_BACKUP_DIR', None)
    if custom:
        _add(custom)
    disk = (os.environ.get('RENDER_DISK_PATH') or '').strip()
    if disk:
        _add(Path(disk) / 'db-backups')
        _add(Path(disk) / 'backups')
    _add(Path(settings.BASE_DIR) / 'backups')
    return [path for path in dirs if path.is_dir()]


def _on_render() -> bool:
    return bool(
        (os.environ.get('RENDER_EXTERNAL_HOSTNAME') or '').strip()
        or (os.environ.get('RENDER') or '').strip()
    )


def backup_storage_status() -> dict:
    """Gdje stoje backupi i da li prežive deploy."""
    disk = (os.environ.get('RENDER_DISK_PATH') or '').strip()
    disk_path = Path(disk) if disk else None
    disk_ok = bool(disk_path and os.path.ismount(disk_path))
    try:
        root = backup_root(create=False)
    except OSError:
        root = backup_root(create=False)
    persistent = False
    if disk_ok:
        try:
            persistent = root.resolve().is_relative_to(disk_path.resolve())
        except (OSError, ValueError, AttributeError):
            persistent = str(root).startswith(str(disk_path))
    on_render = _on_render()
    warning = ''
    if on_render and not persistent:
        warning = (
            'Backupi nisu na trajnom disku servera. Nestaju pri svakom deployu ili restartu. '
            'Preuzmi fajl na svoj računar i čuvaj ga lokalno.'
        )
    try:
        kind = engine_kind()
    except BackupError:
        kind = ''
    return {
        'root': str(root),
        'persistent': persistent,
        'on_render': on_render,
        'kind': kind,
        'warning': warning,
    }


def absorb_ephemeral_backups() -> int:
    """Prebaci backup-e iz foldera aplikacije na trajni disk, ako postoji."""
    if getattr(settings, 'MAGACIN_BACKUP_DIR', None):
        return 0
    disk = (os.environ.get('RENDER_DISK_PATH') or '').strip()
    if not disk or not backup_storage_status()['persistent']:
        return 0
    try:
        persistent = (Path(disk) / 'db-backups').resolve()
        persistent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    moved = 0
    for folder in backup_search_dirs():
        try:
            if folder.resolve() == persistent:
                continue
        except OSError:
            continue
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for path in entries:
            if not _is_backup_file(path):
                continue
            dest = persistent / path.name
            if dest.exists():
                continue
            try:
                # Exclusive copy: never replace an existing snapshot or leave
                # an incomplete file visible in the restore list.
                partial = persistent / (path.name + '.' + uuid.uuid4().hex + '.partial')
                validate_backup(path, 'sqlite' if path.suffix == '.sqlite3' else 'postgres')
                shutil.copy2(path, partial)
                _publish_backup(partial, dest, 'sqlite' if path.suffix == '.sqlite3' else 'postgres')
                moved += 1
            except (OSError, BackupError):
                continue
    return moved


def save_uploaded_backup(uploaded) -> dict:
    """Sačuvaj uploadani .sqlite3 / .dump u folder backup-a."""
    original = Path(getattr(uploaded, 'name', '') or '').name
    lower = original.lower()
    if lower.endswith('.sqlite3'):
        suffix = '.sqlite3'
        prefix = 'db'
    elif lower.endswith('.dump'):
        suffix = '.dump'
        prefix = 'postgres'
    else:
        raise BackupError('Fajl mora biti .sqlite3 (lokalna baza) ili .dump (sajt / Postgres).')
    safe = f'{prefix}-upload-{_stamp()}-{uuid.uuid4().hex}{suffix}'
    root = backup_root()
    dest = root / safe
    partial = root / (safe + '.partial')
    try:
        with partial.open('xb') as out:
            if hasattr(uploaded, 'chunks'):
                for chunk in uploaded.chunks():
                    out.write(chunk)
            else:
                out.write(uploaded.read() if hasattr(uploaded, 'read') else uploaded)
        return _publish_backup(partial, dest, 'sqlite' if suffix == '.sqlite3' else 'postgres')
    except OSError as exc:
        raise BackupError('Upload nije sačuvan. Provjeri prostor i dozvole diska.') from exc


def _is_backup_file(path: Path) -> bool:
    if not path.is_file():
        return False
    return bool(BACKUP_FILE_RE.match(path.name))


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
            src = sqlite3.connect(live.as_uri() + '?mode=ro', uri=True)
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
        # Keep the incomplete file for diagnosis; it is never listed for restore.
        raise BackupError(
            f'pg_dump nije uspio (exit {exc.returncode}). {exc.stderr or exc.stdout or ""}'.strip()
        ) from exc
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise BackupError('Backup fajl je prazan.')
    return dest


def _restore_sqlite(src_path: Path) -> None:
    import sqlite3
    # SQLite's backup API replaces the destination in a transaction and handles
    # existing WAL connections without unlinking files beneath other workers.
    src = sqlite3.connect(src_path.resolve().as_uri() + '?mode=ro', uri=True)
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
        '--single-transaction',
        '--exit-on-error',
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


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_backup(path: Path, kind: str) -> None:
    """Reject damaged copies before touching the live database."""
    import sqlite3
    try:
        checksum = Path(str(path) + '.sha256')
        if checksum.exists() and checksum.read_text().strip() != _checksum(path):
            raise BackupError('Backup je oštećen: kontrolni zbir se ne podudara.')
        if kind == 'sqlite':
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise BackupError('SQLite backup nije ispravan.')
                if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
                    raise BackupError('Backup ne sadrži tabele.')
        else:
            command = shutil.which('pg_restore')
            if not command:
                raise BackupError('pg_restore je potreban za provjeru backupa.')
            subprocess.run([command, '--list', str(path)], check=True, capture_output=True)
    except (OSError, sqlite3.DatabaseError, subprocess.CalledProcessError) as exc:
        raise BackupError('Backup nije prošao provjeru ispravnosti.') from exc


def _publish_backup(partial: Path, dest: Path, kind: str) -> dict:
    validate_backup(partial, kind)
    with partial.open('rb') as source:
        os.fsync(source.fileno())
    # Exclusive link publishes the completed snapshot without ever replacing a file.
    checksum = Path(str(dest) + '.sha256')
    with checksum.open('x') as output:
        output.write(_checksum(partial) + '\n')
        output.flush()
        os.fsync(output.fileno())
    os.link(partial, dest)
    partial.unlink()
    descriptor = os.open(dest.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _info(dest)


def create_backup(*, out_dir: Path | str | None = None, keep=None, protect=None,
                  require_durable: bool = True) -> dict:
    """Napravi provjeren novi backup. Stari se nikad ne brišu ni prepisuju."""
    root = Path(out_dir).expanduser().resolve() if out_dir else backup_root(create=False)
    if require_durable and _on_render():
        disk = (os.environ.get('RENDER_DISK_PATH') or '').strip()
        if not disk or not os.path.ismount(disk) or not root.is_relative_to(Path(disk).resolve()):
            raise BackupError('Backup na server nije sačuvan: potreban je montiran trajni disk. '
                              'Preuzmi backup na računar dok se trajni disk ne podesi.')
    kind = engine_kind()
    name = f"{'db' if kind == 'sqlite' else 'postgres'}-{_stamp()}-{uuid.uuid4().hex}"
    dest = root / (name + ('.sqlite3' if kind == 'sqlite' else '.dump'))
    partial = root / (dest.name + '.partial')
    try:
        root.mkdir(parents=True, exist_ok=True)
        if kind == 'sqlite':
            _backup_sqlite(partial)
        else:
            _backup_postgres(partial)
        return _publish_backup(partial, dest, kind)
    except OSError as exc:
        raise BackupError('Backup nije završen. Provjeri prostor i dozvole diska; stare kopije ostaju sačuvane.') from exc


def list_backups(*, out_dir: Path | str | None = None, migrate: bool = True) -> list[dict]:
    try:
        if out_dir:
            roots = [Path(out_dir).expanduser().resolve()]
        else:
            if migrate:
                absorb_ephemeral_backups()
            roots = backup_search_dirs()
    except (OSError, BackupError):
        return []
    found = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for path in entries:
            if not _is_backup_file(path):
                continue
            info = _info(path)
            prev = found.get(info['name'])
            if prev is None:
                found[info['name']] = info
            elif info['created_at'] and (
                not prev.get('created_at') or info['created_at'] >= prev['created_at']
            ):
                found[info['name']] = info
    rows = list(found.values())
    rows.sort(
        key=lambda row: (row['created_at'] is not None, row['created_at'], row['name']),
        reverse=True,
    )
    return rows


def last_backup(*, out_dir: Path | str | None = None) -> dict | None:
    from django.core.cache import cache
    roots = [Path(out_dir)] if out_dir else backup_search_dirs()
    try:
        signature = repr([(str(root), root.stat().st_mtime_ns) for root in roots])
        key = 'backup-last:' + hashlib.sha256(signature.encode()).hexdigest()
        cached = cache.get(key)
        if cached is not None:
            return cached['row']
        rows = list_backups(out_dir=out_dir, migrate=False)
        row = rows[0] if rows else None
        cache.set(key, {'row': row}, 30)
        return row
    except (OSError, BackupError):
        return None


def resolve_backup_file(name: str, *, out_dir: Path | str | None = None) -> Path:
    raw = (name or '').strip()
    if not raw or raw != Path(raw).name or not BACKUP_FILE_RE.match(raw):
        raise BackupError('Nepoznat backup fajl.')
    roots = (
        [Path(out_dir).expanduser().resolve()]
        if out_dir
        else backup_search_dirs()
    )
    for root in roots:
        if not root.is_dir():
            continue
        path = (root / raw).resolve()
        try:
            if path.is_relative_to(root) and _is_backup_file(path):
                return path
        except (OSError, ValueError):
            continue
    raise BackupError('Backup fajl nije pronađen.')


def restore_backup(name: str, *, out_dir: Path | str | None = None, safety: bool = True) -> dict:
    src = resolve_backup_file(name, out_dir=out_dir)
    kind = engine_kind()
    src_kind = 'sqlite' if src.suffix == '.sqlite3' else 'postgres'
    if src_kind != kind:
        raise BackupError(
            f'Ovaj backup je za {src_kind}, a trenutna baza je {kind}.'
        )
    validate_backup(src, kind)
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
