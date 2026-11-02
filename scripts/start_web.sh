#!/usr/bin/env bash
set -euo pipefail

exec gunicorn EcommerceProject.wsgi:application --workers 1 --threads 4 --timeout 60 --graceful-timeout 20 --max-requests 400 --max-requests-jitter 50
