"""Local stock import only; no dotenv, external services or production databases."""
from transfer_qa_settings import *
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
