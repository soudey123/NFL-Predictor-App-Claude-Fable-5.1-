#!/usr/bin/env bash
# One-shot local runner: creates a venv on first use, installs deps, starts the server.
# Set PORT to force a port; otherwise the first free port from 8000 upward is used.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

port_busy() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

if [ -n "${PORT:-}" ]; then
  port="$PORT"
  if port_busy "$port"; then
    echo "Port $port is already in use by another process:" >&2
    lsof -nP -iTCP:"$port" -sTCP:LISTEN | tail -n +2 >&2
    echo "Pick another with: PORT=<n> ./run.sh" >&2
    exit 1
  fi
else
  port=8000
  while port_busy "$port"; do port=$((port + 1)); done
  [ "$port" -ne 8000 ] && echo "Port 8000 is in use; starting on port $port instead."
fi

echo "NFL Predictor → http://127.0.0.1:$port"
exec ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$port" "$@"
