"""Offline synthetic clone for canonical WMS tests; never production."""
from bera_wms_qa_settings import *
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': '/private/tmp/bera-wms-canonical-qa.sqlite3'}}
