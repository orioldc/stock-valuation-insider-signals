# Machine install (for AI agents and scripts)

This document is the canonical install path for **automated installers** — AI agents (Claude, ChatGPT, Cursor, etc.), CI scripts, and other scripted contexts. The human-facing instructions are in [`README.md`](README.md).

## TL;DR

```bash
curl -fsSL https://raw.githubusercontent.com/orioldc/stock-valuation-insider-signals/main/scripts/bootstrap.sh | bash
```

Exit code 0 ⇒ installed and registered. User must then **restart Claude Desktop** for the new MCP server to appear.

## What the bootstrap does (in order)

| Step | Action | Failure mode | Exit code |
|---|---|---|---|
| 1 | OS check (macOS only) | non-Darwin platform | 10 |
| 2 | Verify Claude Desktop at `/Applications/Claude.app` | not installed | 11 |
| 3 | Verify Homebrew, install missing prereqs: Python 3.11+, Node 20+, xz, jq | brew install fails or brew not present | 12 |
| 4 | Clone repo to `~/.local/share/stock-valuation-insider-signals` | network / git error | 13 |
| 5 | Run `scripts/install.sh` (venv + npm + DB download) | see `logs/install.log` | 14 |
| 6 | Edit `~/Library/Application Support/Claude/claude_desktop_config.json` to register MCP entry | jq error / permission denied | 15 |
| 7 | Verify install sentinel + config entry both present | sanity check failed | 16 |

## Prerequisites this script will install for you (if missing)

- **Homebrew** is required to install missing prereqs. If brew is not present and any prereq is missing, the script bails with a pointer to https://brew.sh.
- **Python 3.11+** — installed via `brew install python@3.12` if missing
- **Node 20+** — installed via `brew install node` if missing
- **xz** — installed via `brew install xz` if missing
- **jq** — installed via `brew install jq` if missing

## Prerequisites that must already be present

- **Claude Desktop** (macOS app from https://www.anthropic.com/download). The script cannot install GUI macOS apps automatically. If missing, exit code 11.
- **Xcode Command Line Tools** (provides git and curl). On macOS run `xcode-select --install` once.

## Result

After successful install:

```
~/.local/share/stock-valuation-insider-signals/
├── .venv/                          ← Python virtualenv with all deps installed
├── data/insider_signals.db         ← decompressed SQLite DB (~866 MB)
├── data/latest_signals.csv         ← scanner output
├── packages/mcp/dist/              ← built MCP server bundle
├── packages/mcp/node_modules/      ← Node deps
├── logs/install.log                ← every install attempt is appended here
├── logs/fastapi.log                ← FastAPI runtime log
└── .install_complete               ← sentinel; existence confirms successful install
```

And in `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "stock-valuation-insider-signals": {
      "command": "/bin/bash",
      "args": ["/Users/<you>/.local/share/stock-valuation-insider-signals/scripts/start.sh"],
      "env": {}
    }
  }
}
```

(Previous entries under `mcpServers` are preserved; only `stock-valuation-insider-signals` is added/replaced. The old config is backed up to `claude_desktop_config.json.bak-YYYYMMDD-HHMMSS`.)

## Verifying success (for the AI agent)

```bash
# 1. Sentinel file present?
test -f ~/.local/share/stock-valuation-insider-signals/.install_complete

# 2. Config registered?
jq -e '.mcpServers["stock-valuation-insider-signals"]' ~/Library/Application\ Support/Claude/claude_desktop_config.json

# 3. Backend can start? (optional smoke test; restart Claude Desktop first)
bash ~/.local/share/stock-valuation-insider-signals/scripts/start.sh &
sleep 5
curl -sf http://localhost:8502/health
```

All three should succeed (exit 0) for a healthy install.

## Updating to the latest release

Re-running the bootstrap script is idempotent:

```bash
curl -fsSL https://raw.githubusercontent.com/orioldc/stock-valuation-insider-signals/main/scripts/bootstrap.sh | bash
```

It will `git pull` the latest code, re-run install.sh (which skips Python/Node deps if the sentinel exists and `--force` is not passed), and re-register the config entry. The user must restart Claude Desktop after each update.

## Uninstalling

```bash
# 1. Remove the config entry
jq 'del(.mcpServers["stock-valuation-insider-signals"])' \
  ~/Library/Application\ Support/Claude/claude_desktop_config.json > /tmp/c.json && \
  mv /tmp/c.json ~/Library/Application\ Support/Claude/claude_desktop_config.json

# 2. Remove the install dir
rm -rf ~/.local/share/stock-valuation-insider-signals

# 3. Restart Claude Desktop
```

## Common failure modes (with recovery)

### Exit 11: Claude Desktop not installed
The AI agent must instruct the user to install Claude Desktop from https://www.anthropic.com/download before re-running the bootstrap.

### Exit 12: prereq install failed (e.g., brew install hung)
Most common cause: stale Homebrew. The AI agent can suggest:
```bash
brew update && brew doctor
# then re-run the bootstrap
```

### Exit 13: git clone failed
Network issue or GitHub rate-limit. Retry after waiting; or instruct the user to check their connection.

### Exit 14: install.sh failed
The AI agent should read `~/.local/share/stock-valuation-insider-signals/logs/install.log` (last ~50 lines) and surface the actual error. Common causes:
- DB download from GitHub Releases failed (network)
- npm install failed (corrupted node cache — `rm -rf packages/mcp/node_modules` and retry)
- pip install failed (PyPI issue — retry)

### Exit 15: config edit failed
Usually a permissions or jq error. The AI agent can:
- Read the existing config to inspect structure
- Manually merge the entry shown in the "Result" section above

### Exit 16: verification failed
Sentinel or config entry missing. AI should inspect:
- `ls ~/.local/share/stock-valuation-insider-signals/.install_complete`
- `cat ~/.local/share/stock-valuation-insider-signals/logs/install.log | tail -30`
- `jq . ~/Library/Application\ Support/Claude/claude_desktop_config.json`

## Custom install location

Set `SVIS_INSTALL_DIR` before running:

```bash
SVIS_INSTALL_DIR=/opt/stock-valuation-insider-signals \
  curl -fsSL https://raw.githubusercontent.com/orioldc/stock-valuation-insider-signals/main/scripts/bootstrap.sh | bash
```

## For ChatGPT / non-executing AI agents

If you can't run shell commands on the user's machine (ChatGPT in browser, etc.), the canonical user-facing instruction is:

> Open Terminal on your Mac and paste this command:
>
> ```
> curl -fsSL https://raw.githubusercontent.com/orioldc/stock-valuation-insider-signals/main/scripts/bootstrap.sh | bash
> ```
>
> When it finishes, quit and re-open Claude Desktop.

That single command handles all prereqs, install, and registration.

## What the AI agent should NOT do

- **Do not** try to download and double-click the `.mcpb` file programmatically. macOS Gatekeeper will block it without user interaction. Use `bootstrap.sh` instead.
- **Do not** hand-edit `claude_desktop_config.json` without backing it up. The bootstrap script does this for you.
- **Do not** install to `/Applications/` or `/usr/local/` — those require sudo. The default install dir is in `$HOME` and never requires elevation.
- **Do not** run `sudo bash bootstrap.sh`. The script does not need sudo and will fail under root because the Claude Desktop config is per-user.
