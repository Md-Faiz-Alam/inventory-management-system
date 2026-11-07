#!/usr/bin/env bash
# Render build step: install, collect static files, migrate, prepare demo data.
set -o errexit

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py createcachetable

# Idempotent: syncs roles, creates demo users from env passwords, loads demo data once.
python manage.py bootstrap_demo
