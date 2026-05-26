#!/usr/bin/env bash
# First-run installer for stock-valuation-insider-signals.
#
# Idempotent: safe to re-run. Skips steps already complete unless --force.
#
# Steps:
#   1. Verify python3 + node are present
#   2. Create .venv and install Python deps
#   3. Install npm deps + build MCP server bundle
#   4. Download latest pre-built SQLite DB from GitHub Releases (anonymous curl)
#   5. Symlink packages/tracker/db/insider_signals.db -> ../../data/insider_signals.db
#   6. Touch sentinel file

set -euo pipefail

# ── path resolution (no hardcoded paths) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SENTINEL="$REPO_ROOT/.install_complete"

# ── log everything to a known location so users can find install errors ──
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/install.log"
# tee stdout+stderr to the log file (append mode keeps history across runs)
exec > >(tee -a "$LOG_FILE") 2>&1
echo
echo "── install.sh started $(date -u +%Y-%m-%dT%H:%M:%SZ) ──"

# Write a failure marker on any error so users / docs can grep for it.
trap '
  rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "[install] FAILED with exit code $rc"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$rc" > "$REPO_ROOT/.install_failed"
    echo "[install] Failure marker written: $REPO_ROOT/.install_failed"
    echo "[install] Full log: $LOG_FILE"
  fi
' EXIT

# ── flags ──
FORCE=0
DB_ONLY=0
SKIP_DB=0
RELEASE_TAG="latest"
while [[ "${1:-}" =~ ^-- ]]; do
  case "$1" in
    --force)    FORCE=1 ;;
    --db-only)  DB_ONLY=1 ;;
    --skip-db)  SKIP_DB=1 ;;
    --release)  shift; RELEASE_TAG="$1" ;;
    *) echo "unknown flag: $1" >&2; exit 1 ;;
  esac
  shift
done

# ── short-circuit ──
if [[ -f "$SENTINEL" && $FORCE -eq 0 && $DB_ONLY -eq 0 ]]; then
  echo "[install] already installed (sentinel: $SENTINEL). Use --force to reinstall."
  exit 0
fi

echo "[install] repo root: $REPO_ROOT"

# ── 1. dependencies present? ──
if [[ $DB_ONLY -eq 0 ]]; then
  command -v python3 >/dev/null 2>&1 || {
    cat >&2 <<EOF
[install] ERROR: python3 not found.
[install]   Install Python 3.11+ from https://python.org/downloads
[install]   After installing, restart Claude Desktop (or run scripts/install.sh again).
EOF
    exit 1
  }
  # Validate Python version: needs 3.11+
  PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)' 2>/dev/null || echo 0)
  if [[ "$PY_OK" != "1" ]]; then
    PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "unknown")
    cat >&2 <<EOF
[install] ERROR: python3 is too old (found $PY_VER, need >=3.11).
[install]   Install a newer Python from https://python.org/downloads
EOF
    exit 1
  fi
  command -v node >/dev/null 2>&1 || {
    cat >&2 <<EOF
[install] ERROR: node not found.
[install]   Install Node 20+ from https://nodejs.org (LTS recommended)
[install]   After installing, restart Claude Desktop.
EOF
    exit 1
  }
  # Validate node version: needs 20+
  NODE_VER=$(node -p 'process.versions.node' 2>/dev/null || echo "unknown")
  NODE_MAJOR=${NODE_VER%%.*}
  if [[ "$NODE_MAJOR" =~ ^[0-9]+$ ]] && (( NODE_MAJOR < 20 )); then
    cat >&2 <<EOF
[install] ERROR: node is too old (found $NODE_VER, need >=20).
[install]   Install a newer Node from https://nodejs.org (LTS recommended)
EOF
    exit 1
  fi
  command -v xz >/dev/null 2>&1 || {
    cat >&2 <<EOF
[install] ERROR: xz not found (needed to decompress the DB snapshot).
[install]   On macOS: brew install xz   (install Homebrew first from https://brew.sh)
[install]   xz ships with macOS Sonoma+ by default; if you have it, you may be on an older macOS.
EOF
    exit 1
  }
fi

# ── 2. python venv + deps ──
if [[ $DB_ONLY -eq 0 ]]; then
  if [[ ! -d "$REPO_ROOT/.venv" || $FORCE -eq 1 ]]; then
    echo "[install] creating .venv …"
    python3 -m venv "$REPO_ROOT/.venv"
  fi
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
  echo "[install] installing Python deps (this may take 1-2 min) …"
  pip install --quiet --upgrade pip
  pip install --quiet -e "$REPO_ROOT"
fi

# ── 3. node deps + MCP build ──
if [[ $DB_ONLY -eq 0 ]]; then
  echo "[install] installing npm deps for MCP server …"
  cd "$REPO_ROOT/packages/mcp"
  npm install --silent
  echo "[install] building MCP server bundle …"
  npm run build --silent
  cd "$REPO_ROOT"
fi

# ── 4. DB snapshot download ──
if [[ $SKIP_DB -eq 0 ]]; then
  DB_PATH="$REPO_ROOT/data/insider_signals.db"
  if [[ -f "$DB_PATH" && $FORCE -eq 0 ]]; then
    echo "[install] DB already present at $DB_PATH (use --force to re-download)"
  else
    echo "[install] downloading DB snapshot ($RELEASE_TAG) from GitHub Releases …"
    REPO_SLUG="orioldc/stock-valuation-insider-signals"
    # When "latest" is requested, find the most recent release whose tag starts
    # with "data-" AND has the insider_signals.db.xz asset. This is more robust
    # than the bare /releases/latest endpoint, which returns the most recently
    # published release of ANY kind — so a code release (v0.1.x) without a DB
    # asset would mask the actual data release.
    if [[ "$RELEASE_TAG" == "latest" ]]; then
      URLS="$(curl -sSL "https://api.github.com/repos/$REPO_SLUG/releases?per_page=30" \
        | python3 -c "
import json, sys
for r in json.load(sys.stdin):
    if not r['tag_name'].startswith('data-') or r['draft'] or r['prerelease']:
        continue
    db = next((a['browser_download_url'] for a in r['assets'] if a['name']=='insider_signals.db.xz'), None)
    if not db: continue
    csv = next((a['browser_download_url'] for a in r['assets'] if a['name']=='latest_signals.csv'), '')
    print(db); print(csv); break
")"
    else
      URLS="$(curl -sSL "https://api.github.com/repos/$REPO_SLUG/releases/tags/$RELEASE_TAG" \
        | python3 -c "
import json, sys
r = json.load(sys.stdin)
db = next((a['browser_download_url'] for a in r['assets'] if a['name']=='insider_signals.db.xz'), '')
csv = next((a['browser_download_url'] for a in r['assets'] if a['name']=='latest_signals.csv'), '')
print(db); print(csv)
")"
    fi
    DB_URL="$(echo "$URLS" | sed -n 1p)"
    CSV_URL="$(echo "$URLS" | sed -n 2p)"
    if [[ -z "$DB_URL" ]]; then
      echo "[install] WARNING: no DB snapshot found in release $RELEASE_TAG. Skipping download."
      echo "[install]          You can rebuild from scratch with: python packages/tracker/run_expanded_pipeline.py"
    else
      mkdir -p "$REPO_ROOT/data"
      echo "[install]   → $DB_URL"
      curl -fL --progress-bar -o "$REPO_ROOT/data/insider_signals.db.xz" "$DB_URL"
      echo "[install] decompressing (this may take 30-60s) …"
      xz -dkf "$REPO_ROOT/data/insider_signals.db.xz"
      rm "$REPO_ROOT/data/insider_signals.db.xz"
      echo "[install]   → $DB_PATH"
      if [[ -n "$CSV_URL" ]]; then
        echo "[install]   → $CSV_URL"
        curl -fL --progress-bar -o "$REPO_ROOT/data/latest_signals.csv" "$CSV_URL"
        echo "[install]   → $REPO_ROOT/data/latest_signals.csv"
      else
        echo "[install] NOTE: latest_signals.csv not present in release; size-adjusted scanner will return empty until refresh."
      fi
    fi
  fi
fi

# ── 5. symlink so tracker code finds the DB at its conventional location ──
if [[ -f "$REPO_ROOT/data/insider_signals.db" ]]; then
  TRACKER_DB_DIR="$REPO_ROOT/packages/tracker/db"
  mkdir -p "$TRACKER_DB_DIR"
  if [[ ! -e "$TRACKER_DB_DIR/insider_signals.db" ]]; then
    ln -sf "../../../data/insider_signals.db" "$TRACKER_DB_DIR/insider_signals.db"
    echo "[install] symlinked packages/tracker/db/insider_signals.db → data/insider_signals.db"
  fi
fi

# ── 6. sentinel ──
if [[ $DB_ONLY -eq 0 ]]; then
  touch "$SENTINEL"
  rm -f "$REPO_ROOT/.install_failed"  # clear any stale failure marker
fi
echo "[install] done."
