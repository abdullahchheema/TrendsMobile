#!/bin/sh
set -eu
cd "$(dirname "$0")"
# All images must already be loaded for a completely offline startup.
python3 control.py up -d --no-build --pull never --wait db
# A power loss during a backup may have left otherwise-running services stopped.
python3 operations.py recover-services
python3 migrate.py --bootstrap-schema
python3 control.py up -d --no-build --pull never --wait db auth rest storage imgproxy meta realtime studio api-gw
python3 migrate.py
python3 provision_database.py
python3 control.py up -d --no-build --pull never --wait crm-web crm-proxy
echo 'Covison Supabase is running. Server address and Studio login are in private/SERVER-LOGIN.txt.'
