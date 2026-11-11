#!/bin/sh
# Entrypoint for infra/Dockerfile.api. Starts as root only to own the mounted volume, then drops to
# the unprivileged `lorde` user: migrations, the job worker (restarted if it exits) and the Gateway.
set -eu
mkdir -p /data/storage /data/work
chown -R lorde:lorde /data
# HOME must be the lorde user's own: libpq reads ~/.postgresql for client certificates and fails on /root.
export HOME=/home/lorde
as_lorde() { setpriv --reuid=10001 --regid=10001 --init-groups "$@"; }
as_lorde python -m gateway.migrate
( while true; do as_lorde python -m worker.main || true; echo "[start-api] worker exited; restarting in 5s"; sleep 5; done ) &
exec setpriv --reuid=10001 --regid=10001 --init-groups \
  uvicorn gateway.app:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips='*' --workers 1
