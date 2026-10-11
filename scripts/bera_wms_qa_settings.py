"""Isolated synthetic WMS QA; never connects to a project or production database."""
from bera_home_qa_settings import *
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': '/private/tmp/bera-wms-qa.sqlite3'}}
