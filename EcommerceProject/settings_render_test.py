"""Opt-in, isolated settings for carpologija-test; never import from production."""
import os
from django.core.exceptions import ImproperlyConfigured

# Fail before importing settings or initializing storage. No dotenv credentials.
os.environ['ECOMMERCE_TEST_MODE'] = 'true'
expected_host = os.environ.get('TEST_DATABASE_HOST', '').strip()
expected_name = os.environ.get('TEST_DATABASE_NAME', '').strip()
if not expected_host or not expected_name or not os.environ.get('DATABASE_URL'):
    raise ImproperlyConfigured('Testni režim zahtijeva DATABASE_URL, TEST_DATABASE_HOST i TEST_DATABASE_NAME.')
secret = os.environ.get('SECRET_KEY', '')
if len(secret) < 50 or secret.startswith('django-insecure-'):
    raise ImproperlyConfigured('Postavite zaseban SECRET_KEY za testni sajt, najmanje 50 znakova.')
if os.environ.get('DEBUG', 'False').lower() not in ('false', '0', 'no'):
    raise ImproperlyConfigured('Render testni sajt zahtijeva DEBUG=False.')

if os.environ.get('SITE_PREP_ENABLED', 'true').lower() not in ('false', '0', 'no') and len(os.environ.get('SITE_PREP_PASSWORD', '')) < 16:
    raise ImproperlyConfigured('Za zaštitu testnog sajta postavite zaseban SITE_PREP_PASSWORD, najmanje 16 znakova.')

# Remove credential fallbacks used directly by scripts and third-party SDKs.
for key in list(os.environ):
    if key.startswith(('R2_', 'AWS_', 'MONRI_', 'MUNGOS_', 'XEXPRESS_', 'OLX_', 'META_', 'XAI_', 'SYNC_', 'TURNSTILE_')) or key in ('EMAIL_APP_PASSWORD', 'EMAIL_HOST_PASSWORD', 'CATALOG_SYNC_API_KEY', 'PARTNER_STOCK_API_KEY'):
        os.environ.pop(key, None)

from .settings import *  # noqa: E402,F403

ECOMMERCE_TEST_MODE = True
configuration = DATABASES['default']
if ('postgresql' not in configuration['ENGINE'] or configuration.get('HOST') != expected_host or configuration.get('NAME') != expected_name):
    raise ImproperlyConfigured('DATABASE_URL ne odgovara deklarisanom hostu/nazivu testne PostgreSQL baze.')
if set(DATABASES) != {'default'}:
    raise ImproperlyConfigured('Testni servis ne smije imati dodatne veze s bazama.')
DEBUG = False
SECRET_KEY = secret
ALLOWED_HOSTS = ['carpologija-test.onrender.com']
CSRF_TRUSTED_ORIGINS = ['https://carpologija-test.onrender.com']
SITE_URL = 'https://carpologija-test.onrender.com'
SEO_CANONICAL_URL = SITE_URL
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
EMAIL_HOST = EMAIL_HOST_USER = EMAIL_HOST_PASSWORD = ''
DEFAULT_FROM_EMAIL = SERVER_EMAIL = ORDER_NOTIFICATION_EMAIL = 'test@example.invalid'
MONRI_ENABLED = MUNGOS_ENABLED = SYNC_ENABLED = False
MONRI_ENVIRONMENT = 'test'
MONRI_PUBLIC_BASE_URL = SITE_URL
for name in ('MONRI_MERCHANT_KEY', 'MONRI_AUTHENTICITY_TOKEN', 'MUNGOS_API_KEY', 'MUNGOS_ECOMMERCE_ACCESS_CODE', 'MUNGOS_BASE_URL', 'XEXPRESS_USERNAME', 'XEXPRESS_PASSWORD', 'XEXPRESS_API_URL', 'OLX_API_TOKEN', 'META_PIXEL_ID', 'META_ACCESS_TOKEN', 'META_TEST_EVENT_CODE', 'SYNC_REMOTE_URL', 'SYNC_API_KEY', 'CATALOG_SYNC_API_KEY', 'PARTNER_STOCK_API_KEY', 'GOOGLE_ANALYTICS_ID', 'GOOGLE_ADS_ID', 'TURNSTILE_SITE_KEY', 'TURNSTILE_SECRET_KEY'):
    globals()[name] = ''
USE_R2_MEDIA = False
MEDIA_URL = '/media/'
STORAGES['default'] = {
    'BACKEND': 'EcommerceApp.storage_backends.RetainingFileSystemStorage',
    'OPTIONS': {'location': str(MEDIA_ROOT), 'base_url': MEDIA_URL},
}
DEFAULT_FILE_STORAGE = STORAGES['default']['BACKEND']
INSTALLED_APPS = [app.replace('EcommerceApp.apps.EcommerceappConfig', 'EcommerceApp.test_safety.TestEcommerceConfig') for app in INSTALLED_APPS]
MIDDLEWARE = ['EcommerceApp.test_safety.TestSafetyMiddleware', *MIDDLEWARE]
