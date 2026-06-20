#!/usr/bin/env bash
# Start Home Utility Intelligence with the local LLM (Ollama) enabled, end to end:
#   1. ensure the Ollama server is running (start it if needed),
#   2. ensure the chosen model is installed (pull it if missing),
#   3. hand off to scripts/dev.sh with OLLAMA_ENABLED=true so the AI Summary /
#      Assistant Chat panels are live.
#
# Usage:  ./scripts/dev-ollama.sh                 (model: llama3.2)
#         OLLAMA_MODEL=qwen3.6:27b ./scripts/dev-ollama.sh
#         HUI_BACKEND_PORT=9000 ./scripts/dev-ollama.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${OLLAMA_MODEL:-llama3.2}"
HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"

if ! command -v ollama >/dev/null 2>&1; then
  echo "ERROR: 'ollama' is not installed. Install it from https://ollama.com/download" >&2
  echo "       then re-run this script (or use ./scripts/dev.sh to run without the LLM)." >&2
  exit 1
fi

# 1. Make sure the Ollama server is up. If not, start it in the background and wait.
STARTED_OLLAMA=""
if ! curl -fsS "$HOST/api/tags" >/dev/null 2>&1; then
  echo "▶ ollama   → starting server ($HOST)"
  ollama serve >/tmp/hui-ollama.log 2>&1 &
  STARTED_OLLAMA=$!
  for _ in $(seq 1 30); do
    curl -fsS "$HOST/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
  if ! curl -fsS "$HOST/api/tags" >/dev/null 2>&1; then
    echo "ERROR: Ollama server did not come up. See /tmp/hui-ollama.log" >&2
    exit 1
  fi
fi
# If we launched the server, stop it again when this script exits.
if [ -n "$STARTED_OLLAMA" ]; then
  trap 'kill "$STARTED_OLLAMA" 2>/dev/null || true' EXIT INT TERM
fi

# 2. Make sure the model is available (treat "x" and "x:latest" as the same tag).
if ! ollama list | awk '{print $1}' | grep -qx -e "$MODEL" -e "${MODEL%:latest}:latest" -e "${MODEL}:latest"; then
  echo "▶ ollama   → pulling model '$MODEL' (one-time download)…"
  ollama pull "$MODEL"
fi

echo "▶ ollama   → ready (model: $MODEL)"

# 3. Hand off to the shared dev runner with the LLM enabled.
export OLLAMA_ENABLED=true
export OLLAMA_MODEL="$MODEL"
export OLLAMA_HOST="$HOST"
exec "$ROOT/scripts/dev.sh"
