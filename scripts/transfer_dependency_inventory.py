"""Record source routes, shared templates and literal static dependencies without data."""
import ast, json, re
from pathlib import Path
source=Path('/Users/bera1990/BERA-webshop')
app=source/'EcommerceApp'
url_tree=ast.parse((app/'urls.py').read_text())
routes=[]
for node in ast.walk(url_tree):
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='path' and len(node.args)>=2 and isinstance(node.args[0],ast.Constant):
        route=node.args[0].value
        if route=='' or route.startswith(('artikal/','korpa/','narudzba/','panel','nalog/','wms/')):
            routes.append({'route':'/'+route,'view':ast.unparse(node.args[1]),'name':next((k.value.value for k in node.keywords if k.arg=='name' and isinstance(k.value,ast.Constant)),None)})
include=re.compile(r'{%\s*(?:include|extends)\s+[\'\"]([^\'\"]+)[\'\"]')
static=re.compile(r'{%\s*static\s+[\'\"]([^\'\"]+)[\'\"]')
roots={'home':'home.html','product':'product_detail.html','cart':'cart.html','checkout':'checkout.html','panel':'staff/panel.html','settings':'staff/settings_workspace.html','settings_form':'staff/settings.html'}
for p in (app/'template/staff').rglob('*.html'):roots.setdefault(str(p.relative_to(app/'template')),str(p.relative_to(app/'template')))
page_dependencies={}
for key,template in roots.items():
    seen=set(); assets=set(); missing=[]; pending=[template]
    while pending:
        name=pending.pop()
        if name in seen:continue
        seen.add(name);path=app/'template'/name
        if not path.exists():path=app/'templates'/name
        if not path.exists():
            # Django admin templates come from the installed Django dependency.
            missing.append(name);continue
        text=path.read_text();pending.extend(include.findall(text));assets.update(static.findall(text))
    page_dependencies[key]={'root_template':template,'templates':sorted(seen),'static_assets':sorted(assets),'framework_or_external_templates':sorted(missing)}
Path('reports/storefront_dependency_inventory.json').write_text(json.dumps({'source':str(source),'routes':routes,'pages':page_dependencies},indent=2,ensure_ascii=False)+'\n')
print(len(routes),'routes;',len(page_dependencies),'template roots inventoried')
