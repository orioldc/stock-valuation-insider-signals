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

# ── checksum verification helper ──
# Verifies a file against SHA256SUMS. Returns 0 if verified, 1 if mismatch, 2 if checksums absent.
verify_checksum() {
  local file="$1"
  local checksums_file="$2"
  local basename
  basename="$(basename "$file")"

  if [[ ! -f "$checksums_file" ]]; then
    return 2  # checksums file absent
  fi

  # Extract expected hash for this file
  local expected_hash
  expected_hash="$(grep -E "\\s+${basename}\$" "$checksums_file" | awk '{print $1}')"
  if [[ -z "$expected_hash" ]]; then
    echo "[install] WARNING: $basename not found in SHA256SUMS"
    return 2
  fi

  # Compute actual hash (prefer shasum -a 256 on macOS, fallback to sha256sum on Linux)
  local actual_hash
  if command -v shasum >/dev/null 2>&1; then
    actual_hash="$(shasum -a 256 "$file" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    actual_hash="$(sha256sum "$file" | awk '{print $1}')"
  else
    echo "[install] ERROR: neither shasum nor sha256sum found; cannot verify integrity"
    return 1
  fi

  if [[ "$actual_hash" == "$expected_hash" ]]; then
    echo "[install] ✓ checksum verified: $basename"
    return 0
  else
    echo "[install] ERROR: checksum mismatch for $basename"
    echo "[install]   expected: $expected_hash"
    echo "[install]   actual:   $actual_hash"
    return 1
  fi
}

# ── database sanity check ──
# Verifies that a SQLite database is structurally sound and has expected tables/data.
sanity_check_db() {
  local db_file="$1"

  echo "[install] running sanity checks on database …"

  # Check that file opens as SQLite
  if ! sqlite3 "$db_file" "PRAGMA quick_check;" >/dev/null 2>&1; then
    echo "[install] ERROR: database failed SQLite integrity check"
    return 1
  fi

  # Check core tables exist
  local tables
  tables="$(sqlite3 "$db_file" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" 2>/dev/null || echo "")"
  local required_tables=("companies" "insider_transactions" "prices" "shares_outstanding")
  for tbl in "${required_tables[@]}"; do
    if ! echo "$tables" | grep -qx "$tbl"; then
      echo "[install] ERROR: missing required table: $tbl"
      return 1
    fi
  done

  # Check that core tables have rows
  local row_counts
  row_counts="$(sqlite3 "$db_file" "
    SELECT
      (SELECT COUNT(*) FROM companies) as companies,
      (SELECT COUNT(*) FROM insider_transactions) as txns,
      (SELECT COUNT(*) FROM prices) as prices,
      (SELECT COUNT(*) FROM shares_outstanding) as shares
  " 2>/dev/null || echo "0|0|0|0")"

  local companies_count txns_count prices_count shares_count
  companies_count="$(echo "$row_counts" | cut -d'|' -f1)"
  txns_count="$(echo "$row_counts" | cut -d'|' -f2)"
  prices_count="$(echo "$row_counts" | cut -d'|' -f3)"
  shares_count="$(echo "$row_counts" | cut -d'|' -f4)"

  if [[ "$companies_count" -eq 0 ]]; then
    echo "[install] ERROR: companies table is empty"
    return 1
  fi
  if [[ "$txns_count" -eq 0 ]]; then
    echo "[install] ERROR: insider_transactions table is empty"
    return 1
  fi
  if [[ "$prices_count" -eq 0 ]]; then
    echo "[install] ERROR: prices table is empty"
    return 1
  fi
  if [[ "$shares_count" -eq 0 ]]; then
    echo "[install] ERROR: shares_outstanding table is empty"
    return 1
  fi

  echo "[install] ✓ database sanity check passed ($companies_count companies, $txns_count transactions, $prices_count prices, $shares_count shares)"
  return 0
}

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
      RELEASE_INFO="$(curl -sSL "https://api.github.com/repos/$REPO_SLUG/releases?per_page=30" \
        | python3 -c "
import json, sys
for r in json.load(sys.stdin):
    if not r['tag_name'].startswith('data-') or r['draft'] or r['prerelease']:
        continue
    db = next((a['browser_download_url'] for a in r['assets'] if a['name']=='insider_signals.db.xz'), None)
    if not db: continue
    csv = next((a['browser_download_url'] for a in r['assets'] if a['name']=='latest_signals.csv'), '')
    hist_csv = next((a['browser_download_url'] for a in r['assets'] if a['name']=='historical_clusters.csv'), '')
    frozen_json = next((a['browser_download_url'] for a in r['assets'] if a['name']=='insider_frozen.json.gz'), '')
    checksums = next((a['browser_download_url'] for a in r['assets'] if a['name']=='SHA256SUMS'), '')
    print(r['tag_name']); print(db); print(csv); print(hist_csv); print(frozen_json); print(checksums); break
")"
      RESOLVED_TAG="$(echo "$RELEASE_INFO" | sed -n 1p)"
      DB_URL="$(echo "$RELEASE_INFO" | sed -n 2p)"
      CSV_URL="$(echo "$RELEASE_INFO" | sed -n 3p)"
      HIST_CSV_URL="$(echo "$RELEASE_INFO" | sed -n 4p)"
      FROZEN_JSON_URL="$(echo "$RELEASE_INFO" | sed -n 5p)"
      CHECKSUMS_URL="$(echo "$RELEASE_INFO" | sed -n 6p)"
    else
      RESOLVED_TAG="$RELEASE_TAG"
      URLS="$(curl -sSL "https://api.github.com/repos/$REPO_SLUG/releases/tags/$RELEASE_TAG" \
        | python3 -c "
import json, sys
r = json.load(sys.stdin)
db = next((a['browser_download_url'] for a in r['assets'] if a['name']=='insider_signals.db.xz'), '')
csv = next((a['browser_download_url'] for a in r['assets'] if a['name']=='latest_signals.csv'), '')
hist_csv = next((a['browser_download_url'] for a in r['assets'] if a['name']=='historical_clusters.csv'), '')
frozen_json = next((a['browser_download_url'] for a in r['assets'] if a['name']=='insider_frozen.json.gz'), '')
checksums = next((a['browser_download_url'] for a in r['assets'] if a['name']=='SHA256SUMS'), '')
print(db); print(csv); print(hist_csv); print(frozen_json); print(checksums)
")"
      DB_URL="$(echo "$URLS" | sed -n 1p)"
      CSV_URL="$(echo "$URLS" | sed -n 2p)"
      HIST_CSV_URL="$(echo "$URLS" | sed -n 3p)"
      FROZEN_JSON_URL="$(echo "$URLS" | sed -n 4p)"
      CHECKSUMS_URL="$(echo "$URLS" | sed -n 5p)"
    fi
    if [[ -z "$DB_URL" ]]; then
      echo "[install] WARNING: no DB snapshot found in release $RELEASE_TAG. Skipping download."
      echo "[install]          You can rebuild from scratch with: python packages/tracker/run_expanded_pipeline.py"
    else
      mkdir -p "$REPO_ROOT/data"

      # Clean up any stale staging directories from previous crashed runs
      find "$REPO_ROOT/data" -maxdepth 1 -type d -name '.staging.*' -exec rm -rf {} + 2>/dev/null || true

      # Check available disk space (need ~2.5 GB for decompression)
      REQUIRED_SPACE_MB=2560
      if command -v df >/dev/null 2>&1; then
        # macOS df uses 512-byte blocks, Linux may vary; use -k for consistent KB output
        AVAIL_KB=$(df -k "$REPO_ROOT/data" | tail -1 | awk '{print $4}')
        AVAIL_MB=$((AVAIL_KB / 1024))
        if [[ $AVAIL_MB -lt $REQUIRED_SPACE_MB ]]; then
          echo "[install] ERROR: insufficient disk space for database download"
          echo "[install]        Required: ${REQUIRED_SPACE_MB} MB"
          echo "[install]        Available: ${AVAIL_MB} MB"
          echo "[install]        Please free up space and retry."
          exit 1
        fi
        echo "[install] disk space check: ${AVAIL_MB} MB available (need ${REQUIRED_SPACE_MB} MB)"
      fi

      # Create staging directory inside data/ for same-filesystem atomic move
      TEMP_DIR="$(mktemp -d "$REPO_ROOT/data/.staging.XXXXXX")"
      trap 'rm -rf "$TEMP_DIR"' EXIT

      # Download checksums file if present
      CHECKSUMS_FILE=""
      if [[ -n "$CHECKSUMS_URL" ]]; then
        echo "[install]   → $CHECKSUMS_URL"
        if curl -fL --progress-bar -o "$TEMP_DIR/SHA256SUMS" "$CHECKSUMS_URL"; then
          CHECKSUMS_FILE="$TEMP_DIR/SHA256SUMS"
        else
          echo "[install] NOTE: checksum file download failed; integrity verification will be skipped"
        fi
      else
        echo "[install] NOTE: SHA256SUMS not present in release $RESOLVED_TAG; integrity cannot be verified"
      fi

      # Download and verify main database
      echo "[install]   → $DB_URL"
      if ! curl -fL --progress-bar -o "$TEMP_DIR/insider_signals.db.xz" "$DB_URL"; then
        echo "[install] ERROR: database download failed"
        exit 1
      fi

      if [[ -n "$CHECKSUMS_FILE" ]]; then
        if ! verify_checksum "$TEMP_DIR/insider_signals.db.xz" "$CHECKSUMS_FILE"; then
          echo "[install] ERROR: database failed checksum verification"
          echo "[install]        This indicates a corrupted or incomplete download."
          echo "[install]        The existing database (if any) has been left untouched."
          echo "[install]        Please retry the installation."
          exit 1
        fi
      else
        echo "[install] WARNING: proceeding without checksum verification (pre-dates SHA256SUMS)"
      fi

      echo "[install] decompressing (this may take 30-60s) …"
      if ! xz -dkf "$TEMP_DIR/insider_signals.db.xz"; then
        echo "[install] ERROR: decompression failed"
        echo "[install]        The existing database (if any) has been left untouched."
        exit 1
      fi

      # Sanity check the decompressed database
      if ! sanity_check_db "$TEMP_DIR/insider_signals.db"; then
        echo "[install] ERROR: database sanity check failed"
        echo "[install]        The downloaded database appears corrupted or incomplete."
        echo "[install]        The existing database (if any) has been left untouched."
        exit 1
      fi

      # All checks passed — move into place atomically (same filesystem, so this is a rename)
      mv -f "$TEMP_DIR/insider_signals.db" "$DB_PATH"
      echo "[install]   → $DB_PATH"

      # Download optional assets (no checksum verification for these — they're informational)
      if [[ -n "$CSV_URL" ]]; then
        echo "[install]   → $CSV_URL"
        curl -fL --progress-bar -o "$REPO_ROOT/data/latest_signals.csv" "$CSV_URL"
        echo "[install]   → $REPO_ROOT/data/latest_signals.csv"
      else
        echo "[install] NOTE: latest_signals.csv not present in release; size-adjusted scanner will return empty until refresh."
      fi
      if [[ -n "$HIST_CSV_URL" ]]; then
        echo "[install]   → $HIST_CSV_URL"
        curl -fL --progress-bar -o "$REPO_ROOT/data/historical_clusters.csv" "$HIST_CSV_URL"
        echo "[install]   → $REPO_ROOT/data/historical_clusters.csv"
      else
        echo "[install] NOTE: historical_clusters.csv not present in release; historical accuracy scoring will degrade to 0 until backtest runs."
      fi
      if [[ -n "$FROZEN_JSON_URL" ]]; then
        echo "[install]   → $FROZEN_JSON_URL"
        curl -fL --progress-bar -o "$REPO_ROOT/data/insider_frozen.json.gz" "$FROZEN_JSON_URL"
        echo "[install]   → $REPO_ROOT/data/insider_frozen.json.gz"
      else
        echo "[install] NOTE: insider_frozen.json.gz not present in release; frozen fallback will use committed snapshot until monthly build runs."
      fi
      # Record the installed release tag
      if [[ -n "$RESOLVED_TAG" ]]; then
        echo "$RESOLVED_TAG" > "$REPO_ROOT/data/.data_release"
        echo "[install] recorded release tag: $RESOLVED_TAG"
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
