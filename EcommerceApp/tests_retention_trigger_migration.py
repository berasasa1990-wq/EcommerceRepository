"""Regression checks for deploy-time trigger DDL without a production connection."""
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from .retention_triggers import install_history, _ensure_postgres_trigger

migration = importlib.import_module('EcommerceApp.migrations.0311_remove_legacy_cancellation_reason')


def postgres_connection():
    connection = MagicMock()
    connection.vendor = 'postgresql'
    connection.alias = 'default'
    connection.ops.quote_name.side_effect = lambda name: '"' + name + '"'
    connection.introspection.table_names.return_value = [
        'EcommerceApp_order', 'EcommerceApp_systemdatarevision',
    ]
    connection.introspection.get_table_description.return_value = [SimpleNamespace(name='id')]
    connection.introspection.get_constraints.return_value = {'pk': {'primary_key': True, 'columns': ['id']}}
    cursor = connection.cursor.return_value.__enter__.return_value
    return connection, cursor


class HistoryMigrationTests(unittest.TestCase):
    def test_existing_postgres_triggers_require_no_ddl_on_repeated_install(self):
        connection, cursor = postgres_connection()
        def query_result(sql, params=None):
            if 'to_regprocedure' in sql:
                cursor.fetchone.return_value = 'ecommerce_preserve_row()'
            elif 'FROM pg_trigger' in sql:
                cursor.fetchone.return_value = (29, 'O', 'ecommerce_preserve_row', memoryview(b'id\0'), 1)
        cursor.execute.side_effect = query_result
        install_history(connection)
        install_history(connection)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any('DROP TRIGGER' in sql or 'CREATE TRIGGER' in sql or 'CREATE FUNCTION' in sql for sql in statements))

    def test_missing_trigger_is_created_with_timeout_and_serialization(self):
        connection, cursor = postgres_connection()
        cursor.fetchone.side_effect = [None, '0', None]
        with patch('EcommerceApp.retention_triggers.transaction.atomic') as atomic:
            _ensure_postgres_trigger(connection, 'EcommerceApp_order', 'id', 'retain_test')
        atomic.assert_called_once_with(using='default')
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(sum('CREATE TRIGGER' in sql for sql in statements), 1)
        self.assertFalse(any('DROP TRIGGER' in sql for sql in statements))
        self.assertTrue(any('pg_advisory_xact_lock' in sql for sql in statements))
        self.assertTrue(any("'5s'" in sql for sql in statements))
        self.assertIn(('0',), tuple(tuple(call.args[1]) for call in cursor.execute.call_args_list if len(call.args) > 1))

    def test_disabled_trigger_is_not_silently_replaced(self):
        connection, cursor = postgres_connection()
        cursor.fetchone.return_value = (29, 'D', 'ecommerce_preserve_row', b'id\0', 1)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected history trigger'):
            _ensure_postgres_trigger(connection, 'EcommerceApp_order', 'id', 'retain_test')
        self.assertFalse(any('DROP' in call.args[0] for call in cursor.execute.call_args_list))

    def test_postgres_column_removal_archives_reason_without_rebuilding_triggers(self):
        connection, cursor = postgres_connection()
        connection.introspection.get_table_description.return_value = [SimpleNamespace(name='razlog_otkazivanja')]
        cursor.description = [('id',), ('razlog_otkazivanja',)]
        cursor.fetchall.return_value = [(1, 'Saved reason')]
        apps = MagicMock()
        with patch('EcommerceApp.retention_triggers.install_history') as install:
            migration.remove_legacy_reason(apps, SimpleNamespace(connection=connection))
        install.assert_not_called()
        snapshot = apps.get_model.return_value.objects.using.return_value.create.call_args.kwargs
        self.assertEqual(snapshot['after']['razlog_otkazivanja'], 'Saved reason')
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(sum('DROP COLUMN' in sql for sql in statements), 1)
        self.assertFalse(any('TRIGGER' in sql for sql in statements))

    def test_already_removed_column_is_noop(self):
        connection, cursor = postgres_connection()
        connection.introspection.get_table_description.return_value = [SimpleNamespace(name='id')]
        migration.remove_legacy_reason(MagicMock(), SimpleNamespace(connection=connection))
        cursor.execute.assert_not_called()

    def test_sqlite_rebuilds_only_order_history_triggers(self):
        connection, cursor = postgres_connection()
        connection.vendor = 'sqlite'
        connection.introspection.get_table_description.return_value = [SimpleNamespace(name='razlog_otkazivanja')]
        cursor.description = [('id',), ('razlog_otkazivanja',)]
        cursor.fetchall.return_value = []
        with patch('EcommerceApp.retention_triggers.install_history') as install:
            migration.remove_legacy_reason(MagicMock(), SimpleNamespace(connection=connection))
        install.assert_called_once_with(connection, tables=['EcommerceApp_order'])
        drops = [call.args[0] for call in cursor.execute.call_args_list if 'DROP TRIGGER' in call.args[0]]
        self.assertEqual(len(drops), 3)
        self.assertTrue(all('retain_' in sql for sql in drops))
