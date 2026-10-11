"""Read-only source import; preserve destination templates/assets in place."""
import hashlib
import json
import re
import shutil
from pathlib import Path
SOURCE = Path('/Users/bera1990/BERA-webshop/EcommerceApp')
TARGET = Path(__file__).resolve().parent.parent / 'EcommerceApp'
namespace = 'bera_reference/'
manifest = []
for folder in ('template', 'static'):
    for src in sorted((SOURCE / folder).rglob('*')):
        if not src.is_file() or src.suffix not in ({'.html'} if folder == 'template' else {'.css','.js','.svg','.png','.jpg','.jpeg','.webp','.gif','.ico','.woff','.woff2','.ttf','.json','.mp4','.webm'}):
            continue
        relative = src.relative_to(SOURCE / folder)
        dst = TARGET / folder / 'bera_reference' / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        if folder == 'template':
            content = src.read_text()
            content = re.sub(r"({%\s*(?:extends|include)\s+)(['\"])([^'\"]+)\2", lambda m:m[1]+m[2]+namespace+m[3]+m[2], content)
            content = re.sub(r"({%\s*static\s+)(['\"])([^'\"]+)\2", lambda m:m[1]+m[2]+namespace+m[3]+m[2], content)
            content = content.replace('static warehouse_access', 'static bera_warehouse_access').replace('{% load warehouse_access %}', '{% load bera_warehouse_access %}')
            dst.write_text(content)
        else:
            shutil.copyfile(src, dst)
        manifest.append({'source': str(src), 'destination': str(dst), 'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest()})
Path('reports/bera_home_import_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(f'Copied {len(manifest)} files into independent namespaces; existing files retained.')
