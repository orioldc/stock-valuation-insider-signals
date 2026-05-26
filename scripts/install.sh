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
    echo "[install] ERROR: python3 not found. Install Python 3.11+ from https://python.org and re-run." >&2
    exit 1
  }
  command -v node >/dev/null 2>&1 || {
    echo "[install] ERROR: node not found. Install Node 20+ from https://nodejs.org and re-run." >&2
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
    # Resolve the asset URL via GitHub's public anonymous API.
    REPO_SLUG="orioldc/stock-valuation-insider-signals"
    if [[ "$RELEASE_TAG" == "latest" ]]; then
      RELEASE_API="https://api.github.com/repos/$REPO_SLUG/releases/latest"
    else
      RELEASE_API="https://api.github.com/repos/$REPO_SLUG/releases/tags/$RELEASE_TAG"
    fi
    RELEASE_JSON="$(curl -sSL "$RELEASE_API")"
    DB_URL="$(echo "$RELEASE_JSON" | grep -E '"browser_download_url".*insider_signals\.db\.xz"' | head -1 | cut -d '"' -f 4)"
    CSV_URL="$(echo "$RELEASE_JSON" | grep -E '"browser_download_url".*latest_signals\.csv"' | head -1 | cut -d '"' -f 4)"
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
fi
echo "[install] done."
