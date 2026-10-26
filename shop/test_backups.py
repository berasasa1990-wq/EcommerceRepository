from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import backups


class BackupTests(SimpleTestCase):
    def test_snapshot_restore_and_safety_copy(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / 'live.sqlite3'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE django_migrations (id INTEGER)')
                connection.execute('INSERT INTO django_migrations VALUES (1)')
            with override_settings(DATABASE_BACKUP_DIR=Path(directory) / 'copies'), patch.object(backups, 'database_path', return_value=database):
                snapshot = backups.create_backup_if_due()
                self.assertTrue(snapshot.is_file())
                self.assertIsNone(backups.create_backup_if_due())
                with sqlite3.connect(database) as connection:
                    connection.execute('INSERT INTO django_migrations VALUES (2)')
                backups.restore_backup(snapshot.name)
                with sqlite3.connect(database) as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM django_migrations').fetchone()[0], 1)
                copies = backups.list_backups()
                self.assertEqual(len(copies), 2)
                safety = next(item for item in copies if item['safety'])
                with sqlite3.connect(Path(directory) / 'copies' / safety['name']) as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM django_migrations').fetchone()[0], 2)
                with self.assertRaises(ValueError):
                    backups.restore_backup('../live.sqlite3')
                corrupt = Path(directory) / 'copies' / 'backup_20000101_000000_000000.sqlite3'
                corrupt.write_text('invalid')
                with self.assertRaises(sqlite3.DatabaseError):
                    backups.restore_backup(corrupt.name)

    def test_retention_removes_only_backups_older_than_three_days(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        with TemporaryDirectory() as directory, override_settings(DATABASE_BACKUP_DIR=Path(directory)):
            def copy_at(age, suffix=''):
                stamp = (now - age).strftime('%Y%m%d_%H%M%S_%f')
                path = Path(directory) / f'backup_{stamp}{suffix}.sqlite3'
                path.touch()
                return path

            old = copy_at(timedelta(days=4))
            old_safety = copy_at(timedelta(days=4), '_before_restore')
            recent = copy_at(timedelta(days=2))
            boundary = copy_at(timedelta(days=3))
            unrelated = Path(directory) / 'manual.sqlite3'
            unrelated.touch()
            with patch.object(backups.time, 'time', return_value=now.timestamp()):
                backups.prune_backups()
            self.assertFalse(old.exists())
            self.assertFalse(old_safety.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(boundary.exists())
            self.assertTrue(unrelated.exists())
