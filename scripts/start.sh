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

# Keep the data current: when a newer monthly snapshot has been published,
# download it in the background so Claude Desktop is not kept waiting.
# install.sh checks the download (checksum, decompress, sanity check) before it
# replaces the database, so a failed download leaves the current data in place.
# The tag of the installed snapshot is kept in data/.data_release; a missing
# file (installs from before it existed) counts as out of date.
# Set INSIDER_NO_AUTO_UPDATE=1 to only warn instead.
INSTALLED_TAG="$(cat "$REPO_ROOT/data/.data_release" 2>/dev/null || echo "")"
# Same choice as install.sh: the newest data-* release that has the database.
LATEST_TAG="$(curl -m 3 -sSL "https://api.github.com/repos/orioldc/stock-valuation-insider-signals/releases?per_page=30" 2>/dev/null \
  | python3 -c "
import json, sys
try:
    for r in json.load(sys.stdin):
        if not r.get('tag_name', '').startswith('data-') or r.get('draft') or r.get('prerelease'):
            continue
        if any(a.get('name') == 'insider_signals.db.xz' for a in r.get('assets', [])):
            print(r['tag_name'])
            break
except Exception:
    pass
" 2>/dev/null || echo "")"
if [[ -n "$LATEST_TAG" && "$INSTALLED_TAG" != "$LATEST_TAG" ]]; then
  echo "[start] data snapshot is behind (installed=${INSTALLED_TAG:-unknown}, latest=$LATEST_TAG)" >&2
  # A lock left by a crash or reboot is cleared after two hours.
  find "$REPO_ROOT/data" -maxdepth 1 -name .updating -mmin +120 -exec rmdir {} \; 2>/dev/null || true
  if [[ "${INSIDER_NO_AUTO_UPDATE:-0}" == "1" ]]; then
    echo "[start]   Run: bash scripts/install.sh --db-only --force" >&2
  elif mkdir "$REPO_ROOT/data/.updating" 2>/dev/null; then
    # The lock directory stops two launches downloading at the same time.
    echo "[start]   downloading $LATEST_TAG in the background (log: logs/data_update.log)" >&2
    nohup bash -c 'bash "$1/install.sh" --db-only --force; rmdir "$2/data/.updating"' _ \
      "$SCRIPT_DIR" "$REPO_ROOT" > "$LOG_DIR/data_update.log" 2>&1 &
  else
    echo "[start]   an update is already running" >&2
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
