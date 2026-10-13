"""Collect static assets into /tmp only; never use real DB or deployment settings."""
from .bera_home_qa_settings import *

DEBUG = False
STATIC_ROOT = Path('/private/tmp/carp-mobile-static-qa/collected')
STORAGES = {
    **STORAGES,
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
