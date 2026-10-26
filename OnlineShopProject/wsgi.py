"""
WSGI config for OnlineShopProject project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'OnlineShopProject.settings')

application = get_wsgi_application()

from shop.backups import start_backup_worker

start_backup_worker()
