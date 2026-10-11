"""Synthetic local QA only; never load dotenv or use either project's real DB."""
import os
from pathlib import Path
_qa_root = Path(os.environ.get('BERA_QA_ROOT', '/Users/bera1990/Ecommerce'))
_qa_settings = _qa_root / 'EcommerceProject/settings.py'
__file__ = str(_qa_settings)
__package__ = 'EcommerceProject'
exec(compile(_qa_settings.read_text().replace('_ENV_VALUES = _load_env_file()', '_ENV_VALUES = {}'), str(_qa_settings), 'exec'))
DEBUG = True
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': '/private/tmp/ecommerce-transfer-qa.sqlite3'}}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
EMAIL_HOST_PASSWORD = ''
SITE_PREP_ENABLED = False
MONRI_ENABLED = MUNGOS_ENABLED = SYNC_ENABLED = False
USE_R2_MEDIA = False
for _qa_key in ('MONRI_MERCHANT_KEY','MONRI_AUTHENTICITY_TOKEN','META_PIXEL_ID','META_ACCESS_TOKEN','GOOGLE_ANALYTICS_ID','GOOGLE_ADS_ID','TURNSTILE_SITE_KEY','TURNSTILE_SECRET_KEY','XEXPRESS_USERNAME','XEXPRESS_PASSWORD','OLX_API_TOKEN'):
 globals()[_qa_key] = ''
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
MEDIA_ROOT = Path('/private/tmp/bera-home-media')
MEDIA_URL = '/media/'
import requests

def _deny_qa_request(*args, **kwargs):
 raise requests.RequestException('External requests disabled in local QA')
requests.sessions.Session.send = _deny_qa_request
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
