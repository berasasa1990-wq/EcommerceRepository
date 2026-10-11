"""Inspect source literals only. Never load .env or output credential values."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
variables = {}
for base in ('EcommerceApp', 'EcommerceProject', 'scripts'):
    for path in (ROOT / base).rglob('*.py'):
        if any(part in {'snapshots', '__pycache__'} for part in path.parts) or path.name.startswith(('tests', 'test_', 'transfer_', 'warehouse_local')):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            function = ast.unparse(node.func)
            match = function in {'_env', '_mungos_env', 'os.getenv', 'os.environ.get', 'os.environ.setdefault'} or (path.name == 'monri_config.py' and function in {'value', 'environment.get'})
            key = node.args[0]
            if match and isinstance(key, ast.Constant) and isinstance(key.value, str):
                variables.setdefault(key.value, []).append(f'{path.relative_to(ROOT)}:{node.lineno}')
path = ROOT / 'manage.py'
for node in ast.walk(ast.parse(path.read_text())):
    if isinstance(node, ast.Call) and ast.unparse(node.func) == 'os.environ.setdefault' and isinstance(node.args[0], ast.Constant):
        variables.setdefault(node.args[0].value, []).append(f'manage.py:{node.lineno}')
required = {'DJANGO_SETTINGS_MODULE','DATABASE_URL','SECRET_KEY','DEBUG','ALLOWED_HOSTS','CSRF_TRUSTED_ORIGINS','SITE_URL','TEST_DATABASE_HOST','TEST_DATABASE_NAME'}
integrations = ('R2_', 'AWS_', 'EMAIL_', 'MONRI_', 'MUNGOS_', 'XEXPRESS_', 'OLX_', 'META_', 'XAI_', 'SYNC_', 'TURNSTILE_', 'GOOGLE_', 'FACEBOOK_')
rows = [{'key': key, 'category': 'obavezne za testni servis' if key in required else ('produkcijske integracije' if key.startswith(integrations) or key in {'CATALOG_SYNC_API_KEY','PARTNER_STOCK_API_KEY'} else 'opcionalne'), 'references': refs} for key, refs in sorted(variables.items())]
(ROOT / 'reports' / 'environment_inventory.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({category:[row['key'] for row in rows if row['category']==category] for category in sorted({row['category'] for row in rows})},ensure_ascii=False,indent=2))
