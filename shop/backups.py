"""Consistent SQLite snapshots, shared by the panel and hourly server worker."""
from contextlib import closing, contextmanager
from datetime import datetime
import fcntl
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from django.conf import settings

logger = logging.getLogger(__name__)
INTERVAL = 3600
RETENTION = 3 * 24 * 3600
_started = False
_start_lock = threading.Lock()
NAME = re.compile(r'backup_\d{8}_\d{6}_\d{6}(?:_before_restore)?\.sqlite3\Z')


def database_path():
    config = settings.DATABASES['default']
    if config['ENGINE'] != 'django.db.backends.sqlite3' or str(config['NAME']) == ':memory:':
        raise ValueError('Backup podržava samo SQLite bazu spremljenu na disku.')
    return Path(config['NAME'])


def backup_dir():
    directory = Path(getattr(settings, 'DATABASE_BACKUP_DIR', settings.BASE_DIR / 'database_backups'))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextmanager
def backup_lock():
    with (backup_dir() / '.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def list_backups():
    return sorted([
        {'name': path.name,
         'created': datetime.strptime(path.name[7:29], '%Y%m%d_%H%M%S_%f').replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo('Europe/Sarajevo')),
         'size': path.stat().st_size,
         'safety': '_before_restore' in path.name}
        for path in backup_dir().iterdir() if NAME.fullmatch(path.name) and not path.is_symlink()
    ], key=lambda item: item['name'], reverse=True)


def _prune_backups():
    cutoff = time.time() - RETENTION
    for copy in list_backups():
        if copy['created'].timestamp() < cutoff:
            (backup_dir() / copy['name']).unlink(missing_ok=True)


def prune_backups():
    with backup_lock():
        _prune_backups()


def _create_backup(safety=False):
    source_path = database_path()
    if not source_path.is_file():
        raise ValueError('Baza podataka ne postoji.')
    stamp = datetime.now(ZoneInfo('UTC')).strftime('%Y%m%d_%H%M%S_%f')
    target = backup_dir() / f"backup_{stamp}{'_before_restore' if safety else ''}.sqlite3"
    temporary = target.with_suffix('.tmp')
    try:
        with closing(sqlite3.connect(f'{source_path.as_uri()}?mode=ro', uri=True, timeout=30)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def create_backup_if_due():
    with backup_lock():
        copies = list_backups()
        target = None
        if not copies or time.time() - (backup_dir() / copies[0]['name']).stat().st_mtime >= INTERVAL:
            target = _create_backup()
        _prune_backups()
        return target


def restore_backup(name):
    if not NAME.fullmatch(name):
        raise ValueError('Neispravan naziv backupa.')
    with backup_lock():
        source_path = backup_dir() / name
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError('Backup nije pronađen.')
        with closing(sqlite3.connect(f'{source_path.as_uri()}?mode=ro', uri=True, timeout=30)) as source:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup je oštećen.')
            if not source.execute("SELECT 1 FROM sqlite_master WHERE name='django_migrations'").fetchone():
                raise ValueError('Backup ne sadrži bazu aplikacije.')
            _create_backup(safety=True)
            # SQLite backup API replaces the database transactionally, including with WAL enabled.
            with closing(sqlite3.connect(database_path(), timeout=30)) as destination:
                source.backup(destination)


def start_backup_worker():
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    def run():
        while True:
            try:
                create_backup_if_due()
            except Exception:
                logger.exception('Automatski backup baze nije uspio')
            time.sleep(30)
    threading.Thread(target=run, name='database-backup', daemon=True).start()
