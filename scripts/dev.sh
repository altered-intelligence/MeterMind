#!/usr/bin/env bash
# Start the Home Utility Intelligence backend (:8077) and frontend (:5173) together.
# Usage:  ./scripts/dev.sh            (backend on 8077)
#         HUI_BACKEND_PORT=9000 ./scripts/dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${HUI_BACKEND_PORT:-8077}"

# Fail early if the chosen port is taken, with a clear hint.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port $PORT is already in use. Re-run with a free port:" >&2
  echo "  HUI_BACKEND_PORT=9000 ./scripts/dev.sh" >&2
  exit 1
fi

echo "▶ backend  → http://127.0.0.1:$PORT  (docs: /docs)"
( cd "$ROOT/backend" && exec .venv/bin/python -m uvicorn app.main:app --reload --port "$PORT" ) &
BACKEND_PID=$!

# Stop the backend when this script exits (Ctrl-C).
cleanup() { kill "$BACKEND_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "▶ frontend → http://localhost:5173"
cd "$ROOT/frontend"
# Point the Vite proxy at the chosen backend port; CA bundle lets Node trust the proxy.
export VITE_API_TARGET="http://127.0.0.1:$PORT"
[ -f "$ROOT/frontend/.node-ca.pem" ] && export NODE_EXTRA_CA_CERTS="$ROOT/frontend/.node-ca.pem"
# Run in foreground (not exec) so the EXIT/INT trap survives to stop the backend too.
npm run dev
