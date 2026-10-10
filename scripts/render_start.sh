#!/usr/bin/env bash
# Apply the schema to the same database used by the running web service.
set -euo pipefail
python manage.py migrate --noinput
python manage.py shell -c 'from django.db import connection; from EcommerceApp.models import Banner; table = Banner._meta.db_table; assert table in connection.introspection.table_names(), "Nedostaje tabela " + table + ". Provjerite DATABASE_URL i historiju migracija; nemojte koristiti --fake."'
exec gunicorn EcommerceProject.wsgi:application --workers 1 --threads 4 --timeout 60 --graceful-timeout 20 --max-requests 400 --max-requests-jitter 50
