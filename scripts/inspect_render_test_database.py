"""Read-only schema inspection. Invoke manually; never applies migrations."""
import os
import django
if os.environ.get('DJANGO_SETTINGS_MODULE') != 'EcommerceProject.settings_render_test':
    raise SystemExit('Provjera je dozvoljena samo uz settings_render_test.')
django.setup()
from django.apps import apps
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.executor import MigrationExecutor

with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
    loader = MigrationLoader(connection)
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(loader.graph.leaf_nodes())
    tables = set(connection.introspection.table_names())
    missing = sorted({model._meta.db_table for model in apps.get_app_config('EcommerceApp').get_models() if model._meta.managed and not model._meta.proxy} - tables)
    print('Missing application tables:', missing)
    print('Unapplied migrations:', [f'{migration.app_label}.{migration.name}' for migration, backwards in plan if not backwards])
