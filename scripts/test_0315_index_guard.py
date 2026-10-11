"""Offline guard and PostgreSQL name-generation checks; no database connection."""
import django,importlib
from unittest.mock import MagicMock
from types import SimpleNamespace
from pathlib import Path
django.setup()
m=importlib.import_module('EcommerceApp.migrations.0315_wms_canonical_warehouse')
e=MagicMock();e.connection.vendor='postgresql';e._create_index_name.side_effect=lambda table,cols,suffix:'idx_'+cols[0]
e.connection.cursor.return_value.__enter__.return_value.fetchone.return_value=('qa_schema',False)
e.connection.introspection.get_constraints.return_value={'idx_location_id':{'index':True,'unique':False,'primary_key':False,'columns':['archived_location_id']}}
model=SimpleNamespace(_meta=SimpleNamespace(db_table='test_table'))
m.rename_retained_fk_index(e,model,'location_id','archived_location_id');e.execute.assert_called_once()
e.execute.reset_mock();e.connection.introspection.get_constraints.return_value={};m.rename_retained_fk_index(e,model,'location_id','archived_location_id');e.execute.assert_not_called()
e.connection.introspection.get_constraints.return_value={'idx_location_id':{'index':True,'unique':False,'columns':['wrong_column']}}
try:m.rename_retained_fk_index(e,model,'location_id','archived_location_id');raise AssertionError('expected refusal')
except RuntimeError:pass
e.connection.introspection.get_constraints.return_value={'idx_location_id':{'index':True,'unique':False,'columns':['archived_location_id']}}
e.connection.cursor.return_value.__enter__.return_value.fetchone.return_value=('qa_schema',True)
try:m.rename_retained_fk_index(e,model,'location_id','archived_location_id');raise AssertionError('expected collision refusal')
except RuntimeError:pass
print('PASS: index rename, absent-index noop, unexpected-index refusal, target collision refusal')
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.migrations.loader import MigrationLoader
pg=DatabaseWrapper({'ENGINE':'django.db.backends.postgresql','NAME':'offline_only','OPTIONS':{},'TIME_ZONE':None},'offline_only')
state=MigrationLoader(None).project_state(('EcommerceApp','0314_bera_appearance'))
ed=pg.schema_editor(collect_sql=True)
lines=[]
for op in m.Migration.operations:
 if isinstance(op,m.RenameFKWithRetainedIndex):
  model=state.apps.get_model('EcommerceApp',op.model_name)
  col=model._meta.get_field(op.old_name).column
  old=ed._create_index_name(model._meta.db_table,[col],suffix='')
  new=ed._create_index_name(model._meta.db_table,['archived_'+col],suffix='')
  lines.extend([f'-- {model._meta.db_table}: {col} -> archived_{col}',f'ALTER INDEX {ed.quote_name(old)} RENAME TO {ed.quote_name(new)};'])
Path('reports/performance/0315-index-renames.sql').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
