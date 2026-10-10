#!/usr/bin/env bash
# Dedicated test service: do not run migrations or any automatic jobs at startup.
set -euo pipefail
if [ "${DJANGO_SETTINGS_MODULE:-}" != "EcommerceProject.settings_render_test" ]; then
  echo 'Testni servis zahtijeva DJANGO_SETTINGS_MODULE=EcommerceProject.settings_render_test' >&2
  exit 1
fi
exec gunicorn EcommerceProject.wsgi:application --workers 1 --threads 4 --timeout 60 --graceful-timeout 20 --max-requests 400 --max-requests-jitter 50
