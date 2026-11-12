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

# Proxy headers (audit 2, S7): trust X-Forwarded-For only from the proxy hop (Railway's edge reaches the container
# from a private/CGNAT address) and take the right-most hop that is not a proxy; never '*', which made uvicorn take
# the left-most, client-written hop. Override with FORWARDED_ALLOW_IPS if the platform's proxy addresses differ.
# --no-access-log (S10): uvicorn's own access log printed /v1/_storage/<signed token> URLs; the Gateway writes one
# JSON access line per request with the token masked (LORDE_ACCESS_LOG=1).
# --limit-concurrency / --timeout-keep-alive (S4): bound open connections (503 past the limit) and idle keep-alives.
PROXY_HOPS="${FORWARDED_ALLOW_IPS:-127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,fc00::/7}"
setpriv --reuid=10001 --regid=10001 --init-groups \
  uvicorn gateway.app:app --host 0.0.0.0 --port "${PORT:-8080}" --proxy-headers --forwarded-allow-ips="$PROXY_HOPS" \
  --no-access-log --limit-concurrency "${LORDE_LIMIT_CONCURRENCY:-128}" --timeout-keep-alive 5 \
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
