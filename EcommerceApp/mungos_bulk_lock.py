"""One confirmed bulk per database; locks release on process/connection exit."""
from contextlib import contextmanager
import fcntl
import hashlib
import tempfile
from pathlib import Path

from django.core.management.base import CommandError
from django.db import connection

LOCK_ID = 739204183


@contextmanager
def bulk_lock():
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_ID])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise CommandError('Mungos bulk je već pokrenut.')
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [LOCK_ID])
    elif connection.vendor == 'sqlite':
        identity = str(connection.settings_dict['NAME'])
        digest = hashlib.sha256(identity.encode()).hexdigest()
        path = Path(tempfile.gettempdir()) / ('mungos-bulk-' + digest + '.lock')
        with path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CommandError('Mungos bulk je već pokrenut.') from None
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    else:
        raise CommandError('Mungos bulk lock nije podržan za ovu bazu.')
