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

# Check for stale data snapshot (non-fatal, warn only).
if [[ -f "$REPO_ROOT/data/.data_release" ]]; then
  INSTALLED_TAG="$(cat "$REPO_ROOT/data/.data_release" 2>/dev/null || echo "")"
  if [[ -n "$INSTALLED_TAG" ]]; then
    LATEST_TAG="$(curl -m 3 -sSL "https://api.github.com/repos/orioldc/stock-valuation-insider-signals/releases?per_page=30" 2>/dev/null \
      | python3 -c "
import json, sys
try:
    for r in json.load(sys.stdin):
        if r.get('tag_name', '').startswith('data-') and not r.get('draft') and not r.get('prerelease'):
            print(r['tag_name'])
            break
except: pass
" 2>/dev/null || echo "")"
    if [[ -n "$LATEST_TAG" && "$INSTALLED_TAG" != "$LATEST_TAG" ]]; then
      echo "[start] WARNING: data snapshot is behind (installed=$INSTALLED_TAG, latest=$LATEST_TAG)" >&2
      echo "[start]   Run: bash scripts/install.sh --db-only --force" >&2
    fi
  fi
fi

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
