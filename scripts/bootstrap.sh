#!/usr/bin/env bash
# One-line installer for stock-valuation-insider-signals.
#
# Usage (human or AI agent):
#   curl -fsSL https://raw.githubusercontent.com/orioldc/stock-valuation-insider-signals/main/scripts/bootstrap.sh | bash
#
# Or, if you're already in a cloned checkout:
#   bash scripts/bootstrap.sh
#
# What this does:
#   1. Verifies prerequisites (Claude Desktop, Python 3.11+, Node 20+, xz, jq, git, curl).
#      Installs missing ones via Homebrew when possible. Bails with a clear actionable
#      error if a manual install is needed (e.g., Claude Desktop).
#   2. Clones (or updates) the repo into ~/.local/share/stock-valuation-insider-signals.
#   3. Runs the existing scripts/install.sh to create the venv + build the MCP bundle +
#      download the latest data snapshot.
#   4. Registers the MCP server in Claude Desktop's config at:
#        ~/Library/Application Support/Claude/claude_desktop_config.json
#      Backs up the existing file before editing.
#   5. Prints "next steps" — restart Claude Desktop, expected first prompt.
#
# Exit codes (for AI agents):
#   0  success
#   10 unsupported OS (only macOS is supported at this release)
#   11 Claude Desktop not installed and we can't install it via brew
#   12 prereq install failed
#   13 git clone failed
#   14 install.sh failed (see logs/install.log)
#   15 config edit failed
#   16 verification failed

set -euo pipefail

# ── colors (no tput on headless shells) ──
if [[ -t 1 ]]; then
  BOLD=$'\e[1m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RED=$'\e[31m'; RESET=$'\e[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi
say()  { printf "%s[bootstrap]%s %s\n" "$BOLD" "$RESET" "$*" >&2; }
warn() { printf "%s[bootstrap]%s %s\n" "$YELLOW" "$RESET" "$*" >&2; }
err()  { printf "%s[bootstrap]%s %s\n" "$RED" "$RESET" "$*" >&2; }

REPO_SLUG="orioldc/stock-valuation-insider-signals"
REPO_NAME="stock-valuation-insider-signals"
INSTALL_DIR="${SVIS_INSTALL_DIR:-$HOME/.local/share/$REPO_NAME}"
MCP_NAME="stock-valuation-insider-signals"  # key under mcpServers in claude_desktop_config.json

# ── OS check ──
if [[ "$(uname -s)" != "Darwin" ]]; then
  err "This installer currently supports macOS only."
  err "Linux/Windows support is on the roadmap. PRs welcome."
  exit 10
fi

CLAUDE_CONFIG_DIR="$HOME/Library/Application Support/Claude"
CLAUDE_CONFIG="$CLAUDE_CONFIG_DIR/claude_desktop_config.json"

say "stock-valuation-insider-signals one-line installer"
say "install dir: $INSTALL_DIR"

# ── 1. prereqs ──

# Claude Desktop is GUI-installed; we can't install it via brew (it's a closed app).
if [[ ! -d "/Applications/Claude.app" ]]; then
  err "Claude Desktop is not installed."
  err "  Install it from https://www.anthropic.com/download and re-run this script."
  exit 11
fi
say "Claude Desktop: found at /Applications/Claude.app"

# Homebrew — used to install other prereqs. Not strictly required if user has all deps already.
HAVE_BREW=0
if command -v brew >/dev/null 2>&1; then
  HAVE_BREW=1
fi

# Helper: try to install a package via brew, or print install URL if brew not present.
ensure_via_brew() {
  local pkg="$1"; local url="$2"
  if [[ $HAVE_BREW -eq 1 ]]; then
    say "  installing $pkg via Homebrew …"
    brew install "$pkg" >&2 || { err "brew install $pkg failed"; exit 12; }
  else
    err "  $pkg not found and Homebrew is not installed."
    err "  Install Homebrew (https://brew.sh) and re-run, or install $pkg manually from $url"
    exit 12
  fi
}

# Python 3.11+
say "checking Python 3.11+ …"
PY_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY_BIN="$(command -v "$candidate")"
      break
    fi
  fi
done
if [[ -z "$PY_BIN" ]]; then
  say "  no Python 3.11+ found, attempting to install …"
  ensure_via_brew "python@3.12" "https://www.python.org/downloads"
  PY_BIN="$(command -v python3.12 || command -v python3)"
fi
say "  Python: $PY_BIN ($("$PY_BIN" --version 2>&1))"

# Node 20+
say "checking Node 20+ …"
if ! command -v node >/dev/null 2>&1 || [[ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]]; then
  say "  no Node 20+ found, attempting to install …"
  ensure_via_brew "node" "https://nodejs.org"
fi
say "  Node: $(command -v node) ($(node --version 2>&1))"

# xz
say "checking xz …"
if ! command -v xz >/dev/null 2>&1; then
  say "  no xz found, attempting to install …"
  ensure_via_brew "xz" "https://tukaani.org/xz/"
fi
say "  xz: $(command -v xz)"

# jq (used to safely edit the JSON config)
say "checking jq …"
if ! command -v jq >/dev/null 2>&1; then
  say "  no jq found, attempting to install …"
  ensure_via_brew "jq" "https://stedolan.github.io/jq/"
fi
say "  jq: $(command -v jq)"

# git + curl are essentially guaranteed on macOS but check anyway
for tool in git curl; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    err "$tool not found. Install Xcode Command Line Tools: xcode-select --install"
    exit 12
  fi
done

# ── 2. clone / update repo ──
say "cloning/updating $INSTALL_DIR …"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  ( cd "$INSTALL_DIR" && git fetch --depth 1 origin main && git reset --hard origin/main ) >&2 \
    || { err "git update failed"; exit 13; }
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  git clone --depth 1 "https://github.com/$REPO_SLUG.git" "$INSTALL_DIR" >&2 \
    || { err "git clone failed"; exit 13; }
fi
say "  repo at: $INSTALL_DIR ($(cd "$INSTALL_DIR" && git rev-parse --short HEAD))"

# ── 3. run install.sh ──
say "running install.sh …"
bash "$INSTALL_DIR/scripts/install.sh" >&2 \
  || { err "install.sh failed. See $INSTALL_DIR/logs/install.log for details."; exit 14; }

# ── 4. register MCP server in Claude Desktop config ──
say "registering MCP server in Claude Desktop config …"
mkdir -p "$CLAUDE_CONFIG_DIR"

# Initialize config if it doesn't exist
if [[ ! -f "$CLAUDE_CONFIG" ]]; then
  echo '{"mcpServers": {}}' > "$CLAUDE_CONFIG"
fi

# Backup the existing config
BACKUP="$CLAUDE_CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
cp "$CLAUDE_CONFIG" "$BACKUP"
say "  backed up existing config to $BACKUP"

# Merge our entry in. Replace any pre-existing entry under the same MCP_NAME.
TMP="$(mktemp)"
jq \
  --arg name "$MCP_NAME" \
  --arg start "$INSTALL_DIR/scripts/start.sh" \
  '.mcpServers[$name] = {
     "command": "/bin/bash",
     "args": [$start],
     "env": {}
   }' \
  "$CLAUDE_CONFIG" > "$TMP" || { err "jq failed to update config"; rm -f "$TMP"; exit 15; }
mv "$TMP" "$CLAUDE_CONFIG"
say "  registered '$MCP_NAME' → bash $INSTALL_DIR/scripts/start.sh"

# ── 5. verify ──
say "verifying install …"
if [[ ! -f "$INSTALL_DIR/.install_complete" ]]; then
  err "install sentinel missing; install.sh may have failed silently"
  exit 16
fi
if ! jq -e ".mcpServers[\"$MCP_NAME\"]" "$CLAUDE_CONFIG" >/dev/null; then
  err "config registration didn't take"
  exit 16
fi
say "  install sentinel present, config entry registered"

# ── 6. done ──
cat <<EOF >&2

${GREEN}✓ Install complete.${RESET}

Next steps:
  1. ${BOLD}Restart Claude Desktop${RESET} (Cmd+Q, then re-open).
  2. Start a new chat and ask:
       ${BOLD}"What tools do you have from stock-valuation-insider-signals?"${RESET}
     You should see four tools listed: get-signal-scanner, get-cluster-detail,
     get-buyback-status, run-valuation.
  3. Try an actual question:
       ${BOLD}"Has BKNG been buying back its shares?"${RESET}

Where things live:
  - Code:     $INSTALL_DIR
  - Data:     $INSTALL_DIR/data/insider_signals.db
  - Logs:     $INSTALL_DIR/logs/
  - Config:   $CLAUDE_CONFIG
  - Backup:   $BACKUP

Update to the latest version any time:
  ${BOLD}curl -fsSL https://raw.githubusercontent.com/$REPO_SLUG/main/scripts/bootstrap.sh | bash${RESET}

EOF
