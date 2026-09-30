#!/usr/bin/env sh
set -eu
exec gunicorn app:app --workers "${WEB_CONCURRENCY:-1}" --threads "${GUNICORN_THREADS:-8}" --timeout "${GUNICORN_TIMEOUT:-120}" --bind "0.0.0.0:${PORT:-5000}"
