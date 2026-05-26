#!/usr/bin/env bash
# Launch the FastAPI bridge + MCP stdio server.
#
# Called by Claude Desktop when the .dxt is registered. Idempotent: if the
# FastAPI is already up on port 8502, only the MCP node process is started.
#
# Auto-runs install.sh on first launch (sentinel-gated).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SENTINEL="$REPO_ROOT/.install_complete"

# First run? install everything.
if [[ ! -f "$SENTINEL" ]]; then
  echo "[start] first run — running install.sh …" >&2
  bash "$SCRIPT_DIR/install.sh"
fi

PORT="${MCP_API_PORT:-8502}"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

# Start FastAPI bridge if not already on PORT.
if ! lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "[start] launching FastAPI on :$PORT …" >&2
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
  cd "$REPO_ROOT/packages/mcp"
  nohup python -m uvicorn api.main:app --port "$PORT" \
    > "$LOG_DIR/fastapi.log" 2>&1 &
  cd "$REPO_ROOT"
  # Brief readiness wait
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done
fi

# Hand off to the node MCP stdio server (foreground; Claude Desktop owns its lifecycle).
export API_BASE="http://localhost:$PORT"
exec node "$REPO_ROOT/packages/mcp/dist/index.js" --stdio
