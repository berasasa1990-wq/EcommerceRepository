from bera_home_qa_settings import *
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES['staticfiles'] = {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}
