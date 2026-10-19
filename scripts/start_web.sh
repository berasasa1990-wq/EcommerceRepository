#!/usr/bin/env bash
set -euo pipefail

# Both processes use the same database and environment; Gmail never runs in HTTP.
python manage.py watch_company_payments &
payments_pid=$!
web_pid=''
cleanup() {
    kill "$payments_pid" 2>/dev/null || true
    if [ -n "$web_pid" ]; then kill "$web_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

gunicorn EcommerceProject.wsgi:application --workers 1 --threads 4 --timeout 60 --graceful-timeout 20 --max-requests 400 --max-requests-jitter 50 &
web_pid=$!
wait "$web_pid"
