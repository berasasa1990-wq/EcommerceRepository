"""Database-free test-mode checks; no migrations, network, or customer data."""
import os
from unittest.mock import patch
import django

django.setup()
from django.conf import settings
from django.core.mail import send_mail
from django.db import connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState
from django.apps import apps
from django.test import RequestFactory
from EcommerceApp.test_safety import TestSafetyMiddleware
from EcommerceApp.emails import queue_order_emails, queue_admin_order_notification
from EcommerceApp.online_gift import queue_scratch_coupon_email
import requests

assert settings.DEBUG is False
assert settings.ALLOWED_HOSTS == ['carpologija-test.onrender.com']
assert settings.CSRF_TRUSTED_ORIGINS == ['https://carpologija-test.onrender.com']
assert not settings.MONRI_ENABLED and not settings.USE_R2_MEDIA and not settings.MUNGOS_ENABLED
assert settings.EMAIL_BACKEND == 'django.core.mail.backends.dummy.EmailBackend'
assert not settings.META_PIXEL_ID and not settings.GOOGLE_ANALYTICS_ID
assert not settings.EMAIL_HOST_PASSWORD and not os.environ.get('XAI_API_KEY')
assert send_mail('Synthetic', 'No email sent', 'test@example.invalid', ['test@example.invalid']) == 1
try:
    requests.post('https://example.invalid/blocked')
except requests.RequestException:
    pass
else:
    raise AssertionError('Outbound request was not blocked')
with patch('EcommerceApp.emails.Thread') as thread, patch('EcommerceApp.online_gift.Thread') as gift_thread:
    queue_order_emails(None)
    queue_admin_order_notification(None)
    queue_scratch_coupon_email(0)
    thread.assert_not_called()
    gift_thread.assert_not_called()
response = TestSafetyMiddleware(lambda r: None)(RequestFactory().post('/placanje/monri/potvrda/'))
assert response.status_code == 403 and 'noindex' in response['X-Robots-Tag']
loader = MigrationLoader(None, ignore_no_migrations=True)
changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(graph=loader.graph)
assert all(connection.connection is None for connection in connections.all())
print('Settings, email suppression, HTTP block, background queues and webhook protection: PASS (no DB connection).')
print('Latest EcommerceApp migration:', loader.graph.leaf_nodes('EcommerceApp'))
print('Pending MODEL changes requiring NEW migration:', {key: [migration.name for migration in value] for key, value in changes.items()})

from botocore.httpsession import URLLib3Session
from urllib.request import urlopen
from django.core.management import call_command
from django.core.management.base import CommandError
for operation in (lambda: URLLib3Session().send(None), lambda: urlopen('https://example.invalid')):
    try:
        operation()
    except RuntimeError:
        pass
    else:
        raise AssertionError('SDK/urllib outbound call not blocked')
try:
    call_command('backup_r2')
except CommandError:
    pass
else:
    raise AssertionError('Automatic integration command not blocked')

import subprocess
import sys
for label, overrides in (
    ('wrong_database_name', {'TEST_DATABASE_NAME': 'wrong_database'}),
    ('wrong_database_host', {'TEST_DATABASE_HOST': 'wrong.invalid'}),
    ('debug_enabled', {'DEBUG': 'true'}),
    ('weak_secret', {'SECRET_KEY': 'invalid'}),
    ('missing_site_password', {'SITE_PREP_ENABLED': 'true', 'SITE_PREP_PASSWORD': ''}),
):
    result = subprocess.run([sys.executable, '-c', 'import django; django.setup()'], env={**os.environ, **overrides}, capture_output=True)
    assert result.returncode != 0, label
    print('Rejected unsafe configuration:', label)
assert all(connection.connection is None for connection in connections.all())
