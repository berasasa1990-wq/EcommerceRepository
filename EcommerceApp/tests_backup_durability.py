import os
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from . import db_backup as backup


class BackupDurabilityTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.live = self.root / 'live.sqlite3'
        with sqlite3.connect(self.live) as db:
            db.execute('CREATE TABLE example (value TEXT)')
            db.execute("INSERT INTO example VALUES ('sačuvano')")
        self.settings_override = override_settings(MAGACIN_BACKUP_DIR=self.root / 'backups', BASE_DIR=self.root)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        for target, kwargs in [('_sqlite_file_path', {'return_value': self.live}),
                               ('engine_kind', {'return_value': 'sqlite'}),
                               ('_on_render', {'return_value': False})]:
            mock = patch.object(backup, target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def test_all_snapshots_retained_and_recovery_preserves_values(self):
        first = backup.create_backup(keep=1)
        with sqlite3.connect(self.live) as db:
            db.execute("UPDATE example SET value='novo'")
        second = backup.create_backup(keep=1)
        self.assertNotEqual(first['name'], second['name'])
        self.assertEqual(len(backup.list_backups()), 2)
        connection = sqlite3.connect(self.live)
        try:
            with patch.object(backup, '_sqlite_conn', return_value=connection):
                result = backup.restore_backup(first['name'])
            self.assertEqual(connection.execute('SELECT value FROM example').fetchone()[0], 'sačuvano')
        finally:
            connection.close()
        with sqlite3.connect(backup.resolve_backup_file(result['safety'])) as db:
            self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0], 'novo')
        self.assertTrue(second['path'].exists())
        self.assertEqual(len(backup.list_backups()), 3)

    def test_corruption_rejected_before_restore(self):
        snapshot = backup.create_backup()
        # A structurally valid but changed DB must fail its checksum.
        with sqlite3.connect(snapshot['path']) as db:
            db.execute("UPDATE example SET value='tampered'")
        with patch.object(backup, '_restore_sqlite') as restore:
            with self.assertRaises(backup.BackupError):
                backup.restore_backup(snapshot['name'])
            restore.assert_not_called()

    def test_failed_snapshot_is_not_listed_and_old_copy_survives(self):
        snapshot = backup.create_backup()
        def broken(dest):
            dest.write_bytes(b'incomplete')
        with patch.object(backup, '_backup_sqlite', side_effect=broken):
            with self.assertRaises(backup.BackupError):
                backup.create_backup()
        self.assertEqual([row['name'] for row in backup.list_backups()], [snapshot['name']])

    def test_render_directory_alone_is_not_durable_storage(self):
        with patch.object(backup, '_on_render', return_value=True), patch.dict(os.environ, {'RENDER_DISK_PATH': str(self.root)}):
            self.assertFalse(backup.backup_storage_status()['persistent'])
            with self.assertRaises(backup.BackupError):
                backup.create_backup()
            # Emergency direct download remains available.
            self.assertTrue(backup.create_backup(require_durable=False)['path'].exists())

    def test_latest_metadata_does_not_rescan_unchanged_folder(self):
        first = backup.create_backup()
        self.assertEqual(backup.last_backup()['name'], first['name'])
        with patch.object(backup, 'list_backups', side_effect=AssertionError('rescan')):
            self.assertEqual(backup.last_backup()['name'], first['name'])
        second = backup.create_backup()
        self.assertEqual(backup.last_backup()['name'], second['name'])

    @override_settings(AWS_STORAGE_BUCKET_NAME='private-test', R2_BUCKET_NAME='private-test')
    def test_media_backup_never_overwrites_existing_archive(self):
        from .management.commands import backup_r2
        archive = self.root / 'existing.zip'
        archive.write_bytes(b'original archive')
        with patch.object(backup_r2, 'r2_client'), patch.object(backup_r2, 'iter_r2_keys', return_value=[('image.jpg', 1)]):
            with self.assertRaises(FileExistsError):
                backup_r2.build_r2_zip(archive)
        self.assertEqual(archive.read_bytes(), b'original archive')

    def test_failed_postgres_dump_keeps_partial_data_and_old_copies(self):
        import subprocess
        partial = self.root / 'incomplete.dump.partial'
        partial.write_bytes(b'partial dump')
        with patch.object(backup.shutil, 'which', return_value='/mock/pg_dump'), patch.object(backup, '_database_url', return_value='mock-db'), patch.object(backup.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pg_dump', stderr='failure')):
            with self.assertRaises(backup.BackupError):
                backup._backup_postgres(partial)
        self.assertEqual(partial.read_bytes(), b'partial dump')
