#!/bin/bash
# Download the pre-built insider_signals.db from GitHub Releases.
# The DB is xz-compressed (~98MB download, ~863MB decompressed).
#
# Requires: gh CLI (brew install gh && gh auth login)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DB_DIR="$PROJECT_ROOT/db"
DB_FILE="$DB_DIR/insider_signals.db"

if ! command -v gh &>/dev/null; then
    echo "ERROR: gh CLI not found. Install: brew install gh && gh auth login"
    exit 1
fi

if [ -f "$DB_FILE" ]; then
    echo "DB already exists at $DB_FILE ($(du -h "$DB_FILE" | cut -f1))"
    read -rp "Overwrite? [y/N] " answer
    if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
        echo "Skipping download."
        exit 0
    fi
fi

mkdir -p "$DB_DIR"

echo "Downloading pre-built DB (~98MB) from latest GitHub Release..."
gh release download --repo fuertesito91/insider-signal-tracker \
    --pattern 'insider_signals.db.xz' \
    --dir "$DB_DIR" \
    --clobber

echo "Decompressing..."
xz -d -f "$DB_DIR/insider_signals.db.xz"
rm -f "$DB_DIR/insider_signals.db.xz"

echo "Done. DB ready at $DB_FILE ($(du -h "$DB_FILE" | cut -f1))"
