#!/usr/bin/env bash
# GitHub Actions self-hosted runner installer for stock-valuation-insider-signals.
#
# Idempotent: safe to re-run. Reconfigures if already installed unless --skip-config.
#
# Steps:
#   1. Preflight checks (Python 3.11+, disk space, gh auth)
#   2. Download latest runner for detected OS/arch
#   3. Register runner with custom label
#   4. Install as launchd/systemd service
#   5. Verify service is running

set -euo pipefail

# ── path resolution (no hardcoded paths) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Derive repo slug from git remote to construct runner directory outside the working tree.
# This prevents runner binaries and _work checkouts from appearing as untracked files,
# and allows multiple repos to coexist without collision.
REPO_SLUG=$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null \
  | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##' || echo "")

if [[ -z "$REPO_SLUG" ]]; then
  echo "[install-runner] ERROR: failed to determine repository from git remote" >&2
  echo "[install-runner]        Run this from a cloned repository with a GitHub remote" >&2
  exit 1
fi

RUNNER_DIR="${RUNNER_HOME:-$HOME/.github-runner}/${REPO_SLUG}"

# ── log everything to a known location so users can find install errors ──
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/install-runner.log"
# tee stdout+stderr to the log file (append mode keeps history across runs).
# IMPORTANT: Do not add `set -x` or echo credentials anywhere in this script — they would be written to disk.
exec > >(tee -a "$LOG_FILE") 2>&1
echo
echo "── install-runner.sh started $(date -u +%Y-%m-%dT%H:%M:%SZ) ──"

# Write a failure marker on any error so users / docs can grep for it.
trap '
  rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "[install-runner] FAILED with exit code $rc"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$rc" > "$REPO_ROOT/.runner_install_failed"
    echo "[install-runner] Failure marker written: $REPO_ROOT/.runner_install_failed"
    echo "[install-runner] Full log: $LOG_FILE"
  fi
' EXIT

# ── flags ──
UNINSTALL=0
SKIP_CONFIG=0
while [[ "${1:-}" =~ ^-- ]]; do
  case "$1" in
    --uninstall)   UNINSTALL=1 ;;
    --skip-config) SKIP_CONFIG=1 ;;
    --help)
      cat <<EOF
Usage: $0 [options]

Install and configure a GitHub Actions self-hosted runner for this repository.

Options:
  --uninstall    Stop the service, remove it, and deregister the runner
  --skip-config  Skip reconfiguration if runner is already configured
  --help         Show this help message

Examples:
  # Install runner
  bash scripts/install-runner.sh

  # Uninstall runner
  bash scripts/install-runner.sh --uninstall

  # Re-run without reconfiguring (e.g., after an upgrade)
  bash scripts/install-runner.sh --skip-config
EOF
      exit 0
      ;;
    *) echo "[install-runner] unknown flag: $1" >&2; exit 1 ;;
  esac
  shift
done

# ── uninstall path ──
if [[ $UNINSTALL -eq 1 ]]; then
  echo "[install-runner] uninstalling runner …"
  if [[ ! -d "$RUNNER_DIR" ]]; then
    echo "[install-runner] no runner directory found at $RUNNER_DIR; nothing to uninstall"
    exit 0
  fi

  cd "$RUNNER_DIR"

  # Stop and remove the service
  if [[ -f ./svc.sh ]]; then
    echo "[install-runner] stopping service …"
    ./svc.sh stop || true
    echo "[install-runner] removing service …"
    ./svc.sh uninstall || true
  fi

  # Deregister the runner (requires gh auth)
  if [[ -f .runner ]]; then
    echo "[install-runner] deregistering runner …"
    ./config.sh remove --token "$(gh api -X POST repos/{owner}/{repo}/actions/runners/remove-token --jq .token)" || true
  fi

  cd "$REPO_ROOT"
  rm -rf "$RUNNER_DIR"
  echo "[install-runner] runner uninstalled and directory removed"
  exit 0
fi

echo "[install-runner] repo root: $REPO_ROOT"
echo "[install-runner] runner dir: $RUNNER_DIR"

# ── 1. preflight checks ──

# Python 3.11+
command -v python3 >/dev/null 2>&1 || {
  cat >&2 <<EOF
[install-runner] ERROR: python3 not found.
[install-runner]   Install Python 3.11+ from https://python.org/downloads
EOF
  exit 1
}
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,11) else 0)' 2>/dev/null || echo 0)
if [[ "$PY_OK" != "1" ]]; then
  PY_VER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "unknown")
  cat >&2 <<EOF
[install-runner] ERROR: python3 is too old (found $PY_VER, need >=3.11).
[install-runner]   Install a newer Python from https://python.org/downloads
EOF
  exit 1
fi

# Disk space: need at least 15GB free
REQUIRED_SPACE_MB=15360
if command -v df >/dev/null 2>&1; then
  AVAIL_KB=$(df -k "$REPO_ROOT" | tail -1 | awk '{print $4}')
  AVAIL_MB=$((AVAIL_KB / 1024))
  if [[ $AVAIL_MB -lt $REQUIRED_SPACE_MB ]]; then
    echo "[install-runner] ERROR: insufficient disk space for runner installation"
    echo "[install-runner]        Required: ${REQUIRED_SPACE_MB} MB (15 GB)"
    echo "[install-runner]        Available: ${AVAIL_MB} MB"
    echo "[install-runner]        Please free up space and retry."
    exit 1
  fi
  echo "[install-runner] disk space check: ${AVAIL_MB} MB available (need ${REQUIRED_SPACE_MB} MB)"
fi

# gh CLI (needed for token generation)
GH_AUTH_OK=0
if command -v gh >/dev/null 2>&1; then
  if gh auth status >/dev/null 2>&1; then
    GH_AUTH_OK=1
    echo "[install-runner] gh CLI: authenticated"
  else
    echo "[install-runner] WARNING: gh CLI found but not authenticated"
    echo "[install-runner]          Automatic token generation will not work."
    echo "[install-runner]          Run 'gh auth login' to authenticate, or provide token manually when prompted."
  fi
else
  echo "[install-runner] WARNING: gh CLI not found"
  echo "[install-runner]          Install from https://cli.github.com or provide token manually when prompted."
fi

# ── 2. detect OS and architecture ──
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$OS" in
  darwin)  RUNNER_OS="osx" ;;
  linux)   RUNNER_OS="linux" ;;
  *)
    echo "[install-runner] ERROR: unsupported OS: $OS"
    echo "[install-runner]        Supported: macOS (darwin), Linux"
    exit 1
    ;;
esac

case "$ARCH" in
  x86_64)  RUNNER_ARCH="x64" ;;
  arm64|aarch64) RUNNER_ARCH="arm64" ;;
  *)
    echo "[install-runner] ERROR: unsupported architecture: $ARCH"
    echo "[install-runner]        Supported: x86_64, arm64/aarch64"
    exit 1
    ;;
esac

echo "[install-runner] detected platform: $RUNNER_OS-$RUNNER_ARCH"

# ── 3. resolve latest runner version from GitHub API ──
echo "[install-runner] fetching latest runner version from GitHub API …"
RUNNER_VERSION=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest \
  | python3 -c "import json, sys; print(json.load(sys.stdin)['tag_name'].lstrip('v'))" 2>/dev/null || echo "")

if [[ -z "$RUNNER_VERSION" ]]; then
  echo "[install-runner] ERROR: failed to fetch latest runner version from GitHub API"
  exit 1
fi

echo "[install-runner] latest runner version: $RUNNER_VERSION"

# ── 4. download runner tarball ──
RUNNER_TARBALL="actions-runner-${RUNNER_OS}-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
DOWNLOAD_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TARBALL}"

mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

# Check if already configured — verify it is for the correct repository to avoid
# reconfiguring a runner from a different repo that happens to share the same slug
# (e.g., two forks with different owners).
if [[ -f .runner ]]; then
  # The .runner file is JSON; extract the repository URL to verify ownership
  EXISTING_REPO_URL=$(python3 -c "import json; print(json.load(open('.runner'))['gitHubUrl'])" 2>/dev/null || echo "")
  EXPECTED_REPO_URL="https://github.com/$REPO_SLUG"

  if [[ "$EXISTING_REPO_URL" != "$EXPECTED_REPO_URL" ]]; then
    cat >&2 <<EOF
[install-runner] ERROR: This runner directory is registered to a different repository.
[install-runner]        Found:    $EXISTING_REPO_URL
[install-runner]        Expected: $EXPECTED_REPO_URL
[install-runner]
[install-runner] This can happen if two repositories have the same slug (e.g., different forks).
[install-runner] To fix: set RUNNER_HOME to a different path before running this script.
[install-runner]
[install-runner] Example:
[install-runner]   export RUNNER_HOME=\$HOME/.github-runner-fork
[install-runner]   bash scripts/install-runner.sh
EOF
    exit 1
  fi

  if [[ $SKIP_CONFIG -eq 1 ]]; then
    echo "[install-runner] runner already configured and --skip-config specified; skipping download and config"
    echo "[install-runner] to reconfigure, run without --skip-config or run --uninstall first"
  else
    echo "[install-runner] runner already configured; will reconfigure"
    # Stop service before reconfiguring
    if [[ -f ./svc.sh ]]; then
      echo "[install-runner] stopping existing service …"
      ./svc.sh stop || true
    fi
    # Remove old configuration
    if [[ -f ./config.sh ]]; then
      echo "[install-runner] removing old configuration …"
      if [[ $GH_AUTH_OK -eq 1 ]]; then
        REMOVE_TOKEN=$(gh api -X POST repos/{owner}/{repo}/actions/runners/remove-token --jq .token 2>/dev/null || echo "")
        if [[ -n "$REMOVE_TOKEN" ]]; then
          ./config.sh remove --token "$REMOVE_TOKEN" || true
        else
          echo "[install-runner] WARNING: failed to get removal token; skipping deregistration"
        fi
      else
        echo "[install-runner] WARNING: gh CLI not authenticated; skipping deregistration"
      fi
    fi
  fi
fi

# Download and extract if not already present or if reconfiguring
if [[ ! -f ./bin/Runner.Listener || $SKIP_CONFIG -eq 0 ]]; then
  echo "[install-runner] downloading runner tarball …"
  echo "[install-runner]   → $DOWNLOAD_URL"
  if ! curl -fL --progress-bar -o "$RUNNER_TARBALL" "$DOWNLOAD_URL"; then
    echo "[install-runner] ERROR: failed to download runner tarball"
    exit 1
  fi

  echo "[install-runner] extracting runner …"
  tar xzf "$RUNNER_TARBALL"
  rm -f "$RUNNER_TARBALL"
fi

# ── 5. get registration token ──
if [[ $SKIP_CONFIG -eq 0 ]]; then
  echo "[install-runner] repository: $REPO_SLUG"

  if [[ $GH_AUTH_OK -eq 1 ]]; then
    echo "[install-runner] generating registration token via gh CLI …"

    REG_TOKEN=$(gh api -X POST "repos/$REPO_SLUG/actions/runners/registration-token" --jq .token 2>/dev/null || echo "")

    if [[ -z "$REG_TOKEN" ]]; then
      echo "[install-runner] ERROR: failed to generate registration token via gh CLI"
      echo "[install-runner]        Check that gh is authenticated and you have admin access to the repo"
      exit 1
    fi
  else
    # Fallback to manual token entry
    cat >&2 <<EOF

[install-runner] gh CLI not available or not authenticated.
[install-runner]
[install-runner] To get a registration token:
[install-runner]   1. Go to https://github.com/$REPO_SLUG/settings/actions/runners/new
[install-runner]   2. Copy the token from the 'Configure' section
[install-runner]   3. Paste it below (it expires in 1 hour)
[install-runner]
EOF
    read -rs -p "Registration token: " REG_TOKEN
    echo  # Print newline after silent read

    if [[ -z "$REG_TOKEN" ]]; then
      echo "[install-runner] ERROR: no registration token provided"
      exit 1
    fi
  fi

  # ── 6. configure runner ──
  echo "[install-runner] configuring runner …"

  REPO_URL="https://github.com/$REPO_SLUG"
  RUNNER_NAME="$(hostname -s)-insider-signals"
  LABELS="insider-signals"

  ./config.sh \
    --url "$REPO_URL" \
    --token "$REG_TOKEN" \
    --name "$RUNNER_NAME" \
    --labels "$LABELS" \
    --work "_work" \
    --unattended \
    --replace

  echo "[install-runner] runner configured: $RUNNER_NAME"
  echo "[install-runner] labels: $LABELS, self-hosted"
fi

# ── 7. install service ──
echo "[install-runner] installing runner as a service …"

if [[ ! -f ./svc.sh ]]; then
  echo "[install-runner] ERROR: svc.sh not found; runner extraction may have failed"
  exit 1
fi

./svc.sh install
./svc.sh start

# ── 8. verify service is running ──
echo "[install-runner] verifying service status …"
sleep 2  # Give the service a moment to start

SERVICE_STATUS="unknown"
case "$OS" in
  darwin)
    # macOS launchd
    PLIST_LABEL="actions.runner.$(basename "$REPO_SLUG").$(hostname -s)-insider-signals"
    if launchctl list | grep -q "$PLIST_LABEL"; then
      SERVICE_STATUS="running"
    else
      SERVICE_STATUS="not running"
    fi
    ;;
  linux)
    # Linux systemd
    SERVICE_NAME="actions.runner.$(basename "$REPO_SLUG").$(hostname -s)-insider-signals.service"
    if systemctl --user is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
      SERVICE_STATUS="running"
    elif systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
      SERVICE_STATUS="running"
    else
      SERVICE_STATUS="not running"
    fi
    ;;
esac

if [[ "$SERVICE_STATUS" == "running" ]]; then
  echo "[install-runner] ✓ service is running"
else
  echo "[install-runner] WARNING: service may not be running (status: $SERVICE_STATUS)"
  echo "[install-runner]          Check logs in $RUNNER_DIR/_diag/ for details"
fi

# ── 9. done ──
rm -f "$REPO_ROOT/.runner_install_failed"  # clear any stale failure marker
echo "[install-runner] done."
echo
echo "Next steps:"
echo "  1. Verify the runner appears at: https://github.com/$REPO_SLUG/settings/actions/runners"
echo "  2. The monthly-snapshot workflow will use this runner when triggered"
echo "  3. Check runner status: ./svc.sh status"
echo "  4. View runner logs: tail -f $RUNNER_DIR/_diag/Runner_*.log"
echo
echo "To uninstall: bash scripts/install-runner.sh --uninstall"
