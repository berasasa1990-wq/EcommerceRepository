"""Run with isolated bera_home_qa_settings; roll back all synthetic test changes."""
import json
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.db import transaction
from django.test import Client
from django.test.utils import setup_test_environment
from django.contrib.auth import get_user_model
from EcommerceApp.models import Order

assert str(settings.DATABASES['default']['NAME']) == '/private/tmp/ecommerce-transfer-qa.sqlite3'
setup_test_environment()
results = {}
with transaction.atomic(), patch('EcommerceApp.staff_alerts.notify_purchase'), patch('EcommerceApp.views.track_purchase'), patch('EcommerceApp.views.sync_narudzba'):
    client = Client()
    response = client.get('/artikal/qa-artikal-1/')
    assert response.status_code == 200
    assert response.context['product_description'] is not None
    results['product'] = True
    assert client.post('/artikal/qa-artikal-1/dodaj/', {'quantity': 2}).status_code in (200, 302)
    cart = client.get('/korpa/')
    item = cart.context['cart_items'][0]
    assert item['quantity'] == 2
    key = item['key']
    assert client.post('/korpa/azuriraj/', {f'quantity_{key}': 3}).status_code == 302
    cart = client.get('/korpa/')
    assert cart.context['cart_items'][0]['quantity'] == 3
    results['quantity_update'] = True
    checkout = client.get('/narudzba/')
    assert checkout.status_code == 200
    assert checkout.context['summary']['ukupno'] == cart.context['summary']['ukupno']
    invalid = client.post('/narudzba/', {'telefon': 'invalid'})
    assert invalid.status_code == 200 and invalid.context['form'].errors
    results['checkout_validation_and_totals'] = True
    before = Order.objects.count()
    response = client.post('/narudzba/', {
        'ime_prezime': 'Sinteticki QA kupac', 'email': 'bera-transfer@example.com',
        'telefon': '061234567', 'adresa': 'Testna ulica 1', 'grad': 'Sarajevo',
        'postanski_broj': '71000', 'payment_method': 'cod',
    })
    assert response.status_code == 302, response.status_code
    assert Order.objects.count() == before + 1, response.url
    order = Order.objects.latest('pk')
    assert order.stavke.exists() and order.ukupno == cart.context['summary']['ukupno']
    assert not client.session.get('cart')
    results['order_creation_existing_models'] = True
    client.post('/artikal/qa-artikal-1/dodaj/', {'quantity': 1})
    key = client.get('/korpa/').context['cart_items'][0]['key']
    client.post('/korpa/ukloni/' + key + '/')
    assert not client.get('/korpa/').context['cart_items']
    results['remove_and_empty_cart'] = True
    assert client.get('/narudzba/').status_code == 302
    admin = get_user_model().objects.filter(is_superuser=True).first()
    client.force_login(admin)
    client.post('/artikal/qa-artikal-1/dodaj/', {'quantity': 1})
    for path in ['/artikal/qa-artikal-1/', '/korpa/', '/narudzba/']:
        assert client.get(path).status_code == 200
        old = client.get(path + '?izgled=prethodni')
        assert old.status_code == 200
        assert 'bera_reference/css/bera-cart.css' not in old.content.decode()
    results['authenticated_and_original_templates'] = True
    transaction.set_rollback(True)
Path('reports/bera-storefront-visual/regression.json').write_text(json.dumps(results, indent=2))
print(json.dumps(results))
