"""Isolated QA for panel actions; synthetic writes rolled back."""
import json, logging
from pathlib import Path
from django.test import Client
from django.db import transaction
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.conf import settings
from EcommerceApp.models import Product, Tag
from EcommerceApp.bera_panel_admin import panel_admin_site
from EcommerceApp.bera_panel_settings import panel_model_sections
from django.test import RequestFactory
assert str(settings.DATABASES['default']['NAME']) == '/private/tmp/ecommerce-transfer-qa.sqlite3'
logging.disable(logging.CRITICAL)
results = {}
with transaction.atomic():
    user = get_user_model().objects.filter(is_superuser=True).first()
    c = Client(); c.force_login(user); assert c.get('/panel').status_code == 200
    req = RequestFactory().get('/panel'); req.user = user
    sections = panel_model_sections(req)
    for item in sections:
        response = c.get(item['url'])
        assert response.status_code == 200, (item['key'],response.status_code)
        assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'
        if item['add_url']:
            assert c.get(item['add_url']).status_code == 200, item['key']
    results['model_lists_and_add_forms'] = len(sections)
    base = '/panel/podesavanja/sekcije/EcommerceApp/tag/'
    response = c.post(base + 'add/', {'naziv':'QA Panel Tag', 'slug':'qa-panel-tag','_save':'Save'})
    assert response.status_code == 302, response.status_code
    assert response.url.startswith('/panel/podesavanja/sekcije/'),response.url
    tag = Tag.objects.get(slug='qa-panel-tag')
    response = c.post(base + str(tag.pk) + '/change/', {'naziv':'QA Panel Tag Updated','slug':tag.slug,'_save':'Save'})
    assert response.status_code == 302
    tag.refresh_from_db(); assert tag.naziv.endswith('Updated')
    response = c.post(base + str(tag.pk) + '/delete/', {'post':'yes'})
    assert response.status_code == 302 and not Tag.objects.filter(pk=tag.pk).exists()
    results['create_update_delete_existing_models'] = True
    response = c.get('/panel/podesavanja/pretraga/', {'model':'Product','q':'QA'})
    assert response.status_code == 200
    assert c.post('/panel/podesavanja/logo/').status_code == 400
    response = c.post('/panel/podesavanja/sajt/', {}, HTTP_ACCEPT='application/json')
    assert response.status_code == 400
    results['settings_lookup_and_validation'] = True
    assert Product in admin.site._registry and Product in panel_admin_site._registry
    assert c.get('/admin/EcommerceApp/product/').status_code == 200
    assert c.get('/nalog/admin/').status_code == 200
    results['original_admin_preserved'] = True
    U = get_user_model()
    plain = U.objects.create_user(username='panel-plain-qa')
    staff = U.objects.create_user(username='panel-staff-qa',is_staff=True)
    c.force_login(plain); assert c.get('/panel').status_code == 302
    c.force_login(staff)
    assert c.get('/panel').status_code == 200
    assert c.get('/panel/podesavanja/sekcije/EcommerceApp/product/').status_code == 403
    assert c.post('/panel/podesavanja/logo/').status_code == 403
    assert c.post('/panel/podesavanja/sajt/').status_code == 403
    assert c.get('/wms').status_code == 302
    results['existing_permissions_preserved'] = True
    transaction.set_rollback(True)
Path('reports/bera-panel-visual/regression.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results))
