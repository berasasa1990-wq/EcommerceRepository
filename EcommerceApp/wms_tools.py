"""WMS presentation routes for existing warehouse services; no duplicate stock logic."""
from functools import wraps
import json
from urllib.parse import urlsplit
from django.http import JsonResponse
from django.urls import path, reverse
from .bera_panel_modules import module_locked
from .warehouse_access import warehouse_user_required

# key, title, original path prefix, existing WMS permission switch
TOOLS = (
    ('artikli-u-setu', 'Artikli u setu', 'setovi', 'wms_zalihe'),
    ('uvoz', 'Uvoz', 'uvoz', 'wms_zalihe'),
    ('dnevno-skidanje', 'Dnevno skidanje', 'dnevno-skidanje-mp', 'wms_zalihe'),
    ('stampaj-deklaracije', 'Štampaj deklaracije', 'stampa-deklaracije', 'wms_zalihe'),
    ('rezervni-dijelovi', 'Rezervni dijelovi', 'rezervni-dijelovi', 'wms_zalihe'),
    ('dupli-barkodovi', 'Dupli barkodovi', 'dupli-barkodovi', 'wms_zalihe'),
    ('popis-robe', 'Popis robe', 'popis', 'wms_zalihe'),
    ('fali-u-mp', 'Fali u MP-u', 'fali-na-sajtu', 'wms_zalihe'),
    ('nivelacije', 'Nivelacije', 'nivelacije', 'wms_zalihe'),
    ('kreiraj-ponudu', 'Kreiraj ponudu', 'ponude', 'wms_narudzbe'),
)


DEPENDENCIES = (('popis-robe/test', 'Popis robe', 'popis-test', 'wms_zalihe'),)

def rewrite_urls(value):
    for key, label, old, module in (*TOOLS, *DEPENDENCIES):
        value = value.replace('/nalog/magacin/' + old + '/', '/wms/' + key + '/')
    value = value.replace('/nalog/magacin/stampa-cijena/', '/wms/stampa-cijena/')
    return value.replace('/nalog/magacin/', '/wms/operacije/')


def tool_view(view, key, module, original_name):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        from django.core.exceptions import PermissionDenied
        # Keep original login/role checks on the original view as well.
        if warehouse_user_required(request.user):
            locked = module_locked(module)
            if request.headers.get('X-WMS-Permissions') == '1':
                response = JsonResponse({'redirect': reverse('staff_wms') if locked else None})
                response['Cache-Control'] = 'no-store'
                return response
            if locked:
                raise PermissionDenied('Ova WMS sekcija je zaključana.')
        request.wms_tool_section = key.split('/')[0]
        request.wms_tool_base = 'bera_reference/staff/wms/legacy_tool_base.html'
        old_name = request.resolver_match.url_name
        request.resolver_match.url_name = original_name
        try:
            response = view(request, *args, **kwargs)
            if hasattr(response, 'render') and not response.is_rendered:
                response.render()
            if response.has_header('Location'):
                target = response['Location']
                parsed = urlsplit(target)
                if not parsed.netloc or parsed.netloc == request.get_host():
                    response['Location'] = rewrite_urls(target)
            if not getattr(response, 'streaming', False) and response.get('Content-Type', '').startswith('text/html'):
                response.content = rewrite_urls(response.content.decode(response.charset)).encode(response.charset)
                if response.has_header('Content-Length'):
                    response['Content-Length'] = str(len(response.content))
            elif not getattr(response, 'streaming', False) and response.get('Content-Type', '').startswith('application/json'):
                def update_urls(value, field=''):
                    if isinstance(value, dict):
                        return {name: update_urls(item, name) for name, item in value.items()}
                    if isinstance(value, list):
                        return [update_urls(item, field) for item in value]
                    if isinstance(value, str) and (field.endswith('_url') or field in {'url', 'redirect', 'href', 'next'}):
                        return rewrite_urls(value)
                    return value
                data = json.loads(response.content)
                response.content = json.dumps(update_urls(data), ensure_ascii=False).encode(response.charset)
                if response.has_header('Content-Length'):
                    response['Content-Length'] = str(len(response.content))
            return response
        finally:
            request.resolver_match.url_name = old_name
    return wrapped


def service_patterns(patterns):
    """Wrap shared operations once, at their canonical WMS address."""
    result = []
    for entry in patterns:
        route = entry.pattern._route
        key = 'zalihe'
        module = None
        for tool_key, title, old, tool_module in (*TOOLS, *DEPENDENCIES):
            if route.startswith('wms/' + tool_key + '/'):
                key, module = tool_key, tool_module
                break
        else:
            suffix = route.removeprefix('wms/operacije/')
            if suffix.startswith(('narudzbe/', 'nacrti/')):
                key = 'narudzbe'
            elif suffix.startswith(('pakuj/', 'pakovanje/')):
                key = 'pakovanje'
            elif suffix.startswith('kupci/'):
                key = 'kupci'
        result.append(path(route, tool_view(entry.callback, key, module, entry.name),
                           entry.default_args, name=entry.name))
    return result


def tool_context(request):
    key = getattr(request, 'wms_tool_section', '')
    if not key:
        return {}
    from .views_wms import context
    title = next((title for name, title, old, module in TOOLS if name == key), 'WMS Magacin')
    return {**context(key), 'wms_title': title, 'is_wms_workspace': True}
