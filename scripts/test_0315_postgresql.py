"""Run only against an explicitly supplied LOCAL disposable PostgreSQL database.
Example: WMS_MIGRATION_TEST_DATABASE_URL=postgresql://...@127.0.0.1/test_wms
PYTHONPATH=scripts:. DJANGO_SETTINGS_MODULE=canonical_qa_settings python scripts/test_0315_postgresql.py
Creates and removes its own randomly named schema, never existing tables.
"""
import os, uuid, importlib
from urllib.parse import urlsplit
assert os.environ.get('DJANGO_SETTINGS_MODULE') == 'canonical_qa_settings'
url = os.environ.get('WMS_MIGRATION_TEST_DATABASE_URL', '')
parsed = urlsplit(url)
if parsed.scheme not in ('postgres','postgresql') or parsed.hostname not in ('localhost','127.0.0.1','::1') or not parsed.path.lstrip('/').startswith('test_'):
    raise SystemExit('Provide an isolated LOCAL PostgreSQL test_* database via WMS_MIGRATION_TEST_DATABASE_URL; no production database is allowed.')
import django
django.setup()
import dj_database_url
from django.db import connections, transaction
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone
from decimal import Decimal
alias = 'wms_migration_test'
config = dj_database_url.parse(url)
config.update(ATOMIC_REQUESTS=False,AUTOCOMMIT=True,CONN_MAX_AGE=0,CONN_HEALTH_CHECKS=False,TIME_ZONE=None,TEST={})
config.setdefault('OPTIONS',{})
connections.databases[alias] = config
connection = connections[alias]
schema = 'wms_0315_'+uuid.uuid4().hex
loader = MigrationLoader(None)
state = loader.project_state(('EcommerceApp','0314_bera_appearance'))
patch = importlib.import_module('EcommerceApp.migrations.0315_wms_canonical_warehouse')
models = {}
def include(model):
    if model in models: return
    models[model] = True
    for field in [*model._meta.local_fields,*model._meta.local_many_to_many]:
        if field.is_relation:
            include(field.remote_field.model)
            if field.many_to_many and not field.remote_field.through._meta.auto_created: include(field.remote_field.through)
for name in ('WMSPickLine','WMSTransfer','WMSSettings','WMSOrderItem','WarehouseStock','SiteSettings'):
    include(state.apps.get_model('EcommerceApp',name))
def sample(model, **overrides):
    values = dict(overrides)
    for field in model._meta.local_fields:
        if field.name in values or field.attname in values or field.primary_key or field.null or field.has_default() or getattr(field,'auto_now',False) or getattr(field,'auto_now_add',False): continue
        if field.is_relation: values[field.name] = sample(field.remote_field.model)
        elif field.get_internal_type() in ('DateTimeField','DateField'): values[field.name] = timezone.now()
        elif field.get_internal_type() == 'DecimalField': values[field.name] = Decimal('1')
        elif 'Integer' in field.get_internal_type(): values[field.name] = 1
        elif field.get_internal_type() == 'BooleanField': values[field.name] = False
        elif field.get_internal_type() == 'JSONField': values[field.name] = {}
        else: values[field.name] = 'QA-'+uuid.uuid4().hex[:8]
    return model.objects.using(alias).create(**values)
with connection.cursor() as cursor:
    cursor.execute('CREATE SCHEMA '+connection.ops.quote_name(schema))
    cursor.execute('SET search_path TO '+connection.ops.quote_name(schema))
try:
    with connection.schema_editor() as editor:
        for model in models: editor.create_model(model)
    get = lambda name: state.apps.get_model('EcommerceApp',name)
    original = sample(get('WarehouseLocation'))
    legacy = sample(get('WMSLocation'),legacy_location=original)
    stock = sample(get('WarehouseStock'),location=original,kolicina=17,rezervisano=3)
    order = sample(get('WMSOrder'))
    pick = sample(get('WMSPickLine'),order=order,location=legacy)
    transfer = sample(get('WMSTransfer'),source=legacy,destination=legacy)
    settings = sample(get('WMSSettings'),pk=1,transfer_location=legacy,picking_last_location=legacy)
    unchanged = list(get('WarehouseStock').objects.using(alias).values_list('pk','kolicina','rezervisano'))
    # A missing legacy mapping must roll back the entire PostgreSQL DDL/data migration.
    unmapped = sample(get('WMSLocation'), legacy_location=None)
    extra_pick = sample(get('WMSPickLine'), order=order, location=unmapped)
    try:
        with connection.schema_editor() as editor:
            patch.Migration('0315_wms_canonical_warehouse','EcommerceApp').apply(state,editor)
        raise AssertionError('Unmapped location should reject migration')
    except RuntimeError as error:
        assert 'WMS lokacije' in str(error)
    with connection.cursor() as cursor:
        columns = {column.name for column in connection.introspection.get_table_description(cursor,get('WMSPickLine')._meta.db_table)}
    assert 'location_id' in columns and 'archived_location_id' not in columns
    assert get('WMSPickLine').objects.using(alias).get(pk=extra_pick.pk).location_id == unmapped.pk
    get('WMSLocation').objects.using(alias).filter(pk=unmapped.pk).update(legacy_location_id=original.pk)
    # Real full forward migration, including the five existing old FK indexes.
    with connection.schema_editor() as editor:
        after = patch.Migration('0315_wms_canonical_warehouse','EcommerceApp').apply(state,editor)
    for name, pk, fields in [('WMSPickLine',pick.pk,['location']),('WMSTransfer',transfer.pk,['source','destination']),('WMSSettings',1,['transfer_location','picking_last_location'])]:
        model = after.apps.get_model('EcommerceApp',name)
        row = model.objects.using(alias).get(pk=pk)
        with connection.cursor() as cursor: indexes = connection.introspection.get_constraints(cursor,model._meta.db_table)
        with connection.schema_editor() as editor:
            for field in fields:
                assert getattr(row,field+'_id') == original.pk
                assert getattr(row,'archived_'+field+'_id') == legacy.pk
                for column in (field+'_id','archived_'+field+'_id'):
                    index = editor._create_index_name(model._meta.db_table,[column],suffix='')
                    assert indexes[index]['columns'] == [column]
    assert unchanged == list(after.apps.get_model('EcommerceApp','WarehouseStock').objects.using(alias).values_list('pk','kolicina','rezervisano'))
    Pick = after.apps.get_model('EcommerceApp','WMSPickLine')
    assert all(row.location_id == original.pk for row in Pick.objects.using(alias).all())
    migration_0316 = importlib.import_module('EcommerceApp.migrations.0316_homebestsellerproduct')
    with connection.schema_editor() as editor:
        final = migration_0316.Migration('0316_homebestsellerproduct','EcommerceApp').apply(after,editor)
    assert final.apps.get_model('EcommerceApp','HomeBestsellerProduct').objects.using(alias).count() == 0
    print('PASS: unmapped-location rollback; full PostgreSQL 0314 -> 0315 -> 0316; five index pairs, all pick mappings, archived links and stock/reservations retained')
finally:
    with connection.cursor() as cursor:
        cursor.execute('SET search_path TO pg_catalog')
        cursor.execute('DROP SCHEMA '+connection.ops.quote_name(schema)+' CASCADE')
    connection.close()
