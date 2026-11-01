#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 setup_local.py
if ! docker image inspect covison/offline:development >/dev/null 2>&1; then
  echo 'Load the supplied offline images, or build them once while connected: docker compose build web'
  exit 1
fi
docker compose up -d --no-build --pull never --wait
docker compose exec -T web python manage.py bootstrap_owner
if [ ! -f LOCAL-LOGIN.txt ]; then
  docker compose cp web:/data/initial-owner.txt LOCAL-LOGIN.txt
  chmod 600 LOCAL-LOGIN.txt
fi
echo 'Open http://localhost:8081 — first login details are in LOCAL-LOGIN.txt'
