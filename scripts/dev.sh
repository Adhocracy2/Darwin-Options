#!/usr/bin/env bash
# DEV environment: see changes live without deploying.
#   API  : working copy's Python on :8010, restarts when src/ changes
#   UI   : Vite on :5173 (open http://<this machine>:5173/), updates instantly on save
# Reads ~/darwin/shared (config, journal, .env) like production. Production (:8000) is untouched.
#   scripts/dev.sh          start both in the background (logs in var/dev/)
#   scripts/dev.sh stop     stop both
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SHARED="${DARWIN_SHARED:-$HOME/darwin/shared}"
PY="${DARWIN_PY:-$HOME/darwin/current/.venv/bin/python}"
LOGS="$REPO/var/dev"
mkdir -p "$LOGS"

stop() { fuser -k 8010/tcp 5173/tcp >/dev/null 2>&1 || true; }
if [[ "${1:-}" == "stop" ]]; then stop; echo "dev stopped"; exit 0; fi
stop

set -a; [[ -f "$SHARED/.env" ]] && . "$SHARED/.env"; set +a
unset DARWIN_ALLOW_LIVE  # dev never trades real money

cd "$SHARED"
PYTHONPATH="$REPO/src" setsid "$PY" -m uvicorn dev_app:app --factory --app-dir "$REPO/scripts" \
  --host 127.0.0.1 --port 8010 --reload --reload-dir "$REPO/src" --log-level warning \
  > "$LOGS/api.log" 2>&1 < /dev/null &

cd "$REPO/ui"
DARWIN_API=http://127.0.0.1:8010 setsid npx vite --host 0.0.0.0 --port 5173 --strictPort \
  > "$LOGS/ui.log" 2>&1 < /dev/null &

ip=$(hostname -I | awk '{print $1}')
echo "dev UI: http://$ip:5173/   (API on :8010, logs in $LOGS)"
