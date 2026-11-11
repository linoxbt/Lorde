#!/bin/sh
# Entrypoint for infra/Dockerfile.api. Starts as root only to own the mounted volume, then drops to
# the unprivileged `lorde` user: migrations, the job worker (restarted if it exits) and the Gateway.
#
# This shell stays PID 1 and forwards SIGTERM/SIGINT to both children, so on a deploy the worker stops
# claiming and puts its running job back in the queue (without counting an attempt) and uvicorn drains.
set -eu
mkdir -p /data/storage /data/work
# chown -R only when needed: on a large volume it is slow, and on every boot it is wasted work.
for d in /data /data/storage /data/work; do
  if [ "$(stat -c %u "$d")" != 10001 ]; then chown -R lorde:lorde /data; break; fi
done
# HOME must be the lorde user's own: libpq reads ~/.postgresql for client certificates and fails on /root.
# For DATABASE_URL use sslmode=verify-full with sslrootcert=<Supabase CA> (see gateway/settings.py).
export HOME=/home/lorde
as_lorde() { setpriv --reuid=10001 --regid=10001 --init-groups "$@"; }
as_lorde python -m gateway.migrate

stopping=0
worker_pid=""
(
  # worker supervisor: restarts the worker if it crashes, passes SIGTERM on and exits when told to stop
  child=""
  trap 'stop=1; [ -n "$child" ] && kill -TERM "$child" 2>/dev/null; true' TERM INT
  stop=0
  while [ "$stop" = 0 ]; do
    setpriv --reuid=10001 --regid=10001 --init-groups python -m worker.main &   # setpriv execs: $! is the worker
    child=$!
    # a trapped signal interrupts wait: keep waiting until the worker has really finished its requeue
    while kill -0 "$child" 2>/dev/null; do wait "$child" || true; done
    [ "$stop" = 0 ] || break
    echo "[start-api] worker exited; restarting in 5s"
    sleep 5 &
    wait $! || true
  done
) &
worker_pid=$!

setpriv --reuid=10001 --regid=10001 --init-groups \
  uvicorn gateway.app:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips='*' \
  --workers 1 --timeout-graceful-shutdown 20 &
api_pid=$!

shutdown() {
  stopping=1
  kill -TERM "$worker_pid" "$api_pid" 2>/dev/null || true
}
trap shutdown TERM INT

# Wait for either child; if the API dies on its own, stop the worker too and exit with its status.
status=0
while :; do
  if ! kill -0 "$api_pid" 2>/dev/null; then wait "$api_pid" || status=$?; break; fi
  if [ "$stopping" = 1 ]; then wait "$api_pid" || status=$?; break; fi
  sleep 1 &
  wait $! || true
done
[ "$stopping" = 1 ] || kill -TERM "$worker_pid" 2>/dev/null || true
while kill -0 "$worker_pid" 2>/dev/null; do wait "$worker_pid" || true; done
exit "$status"
