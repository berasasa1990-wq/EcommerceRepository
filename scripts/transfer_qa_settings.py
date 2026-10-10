"""Isolated transfer QA: never loads dotenv, customer DB, uploads or remote services.

Use PYTHONPATH=scripts:. DJANGO_SETTINGS_MODULE=transfer_qa_settings.
TRANSFER_QA_ROOT can point to the read-only reference project.
"""
import os
from pathlib import Path

_QA_ROOT = Path(os.environ.get('TRANSFER_QA_ROOT', Path(__file__).resolve().parent.parent))
_QA_SETTINGS = _QA_ROOT / 'EcommerceProject' / 'settings.py'
__file__ = str(_QA_SETTINGS)
__package__ = 'EcommerceProject'
exec(compile(_QA_SETTINGS.read_text().replace('_ENV_VALUES = _load_env_file()', '_ENV_VALUES = {}'), str(_QA_SETTINGS), 'exec'))

DEBUG = True
SECRET_KEY = 'local-transfer-qa-only-never-deploy'
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ.get('TRANSFER_QA_DB', '/private/tmp/ecommerce-transfer-qa.sqlite3')}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
EMAIL_HOST_PASSWORD = EMAIL_HOST_USER = EMAIL_HOST = ''
DEFAULT_FROM_EMAIL = STORE_EMAIL = ORDER_NOTIFICATION_EMAIL = 'qa@example.invalid'
SITE_URL = SEO_CANONICAL_URL = 'http://127.0.0.1:8011'
SITE_NAME = 'QA Webshop'
STORE_PHONE = MESSENGER_PAGE = ''
GOOGLE_ANALYTICS_ID = GOOGLE_ADS_ID = FACEBOOK_DOMAIN_VERIFICATION = META_PIXEL_ID = META_ACCESS_TOKEN = ''
TURNSTILE_SITE_KEY = TURNSTILE_SECRET_KEY = ''
MUNGOS_ENABLED = MONRI_ENABLED = USE_R2_MEDIA = False
MUNGOS_API_KEY = MONRI_MERCHANT_KEY = MONRI_AUTHENTICITY_TOKEN = OLX_API_TOKEN = XEXPRESS_USERNAME = XEXPRESS_PASSWORD = ''
SECURE_SSL_REDIRECT = SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
MEDIA_ROOT = Path('/private/tmp/ecommerce-transfer-qa-media')
MEDIA_URL = '/media/'
STATIC_ROOT = Path('/private/tmp/ecommerce-transfer-qa-static')
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
SITE_PREP_ENABLED = False
SITE_PREP_PASSWORD = ''
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
TEST_RUNNER = 'transfer_qa_runner.TransferQARunner'
