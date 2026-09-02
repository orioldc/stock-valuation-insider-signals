# Machine Setup for Self-Hosted Runner

The monthly snapshot workflow runs on a self-hosted GitHub Actions runner rather than GitHub's hosted runners. This document explains why, how to set it up, and how to manage it.

## Why Self-Hosted?

The monthly pipeline has two distinct phases:

1. **SEC-based ingestion**: Form 4 filings, shares outstanding, ticker fixes — works fine on GitHub-hosted runners
2. **Yahoo Finance data**: Stock prices, market caps, splits, benchmark ETFs — **blocked on GitHub-hosted runners**

Yahoo Finance blocks GitHub's IP ranges at the network level. In one run, the workflow hit 5,410 rate-limit errors, updated 0 of 3,844 market caps, and left prices/splits/benchmarks as no-ops. The same calls succeed in their thousands from a residential IP.

Rather than work around the block (proxies, API services, etc.), we run the workflow on a machine with an IP Yahoo accepts — typically the repo owner's Mac. SEC-based steps continue to work because the SEC does not block GitHub.

## Prerequisites

Before installing the runner:

- **Python 3.11+**: The pipeline requires Python 3.11 or later
- **15 GB free disk space**: The database is ~900 MB, plus compression workspace and runner overhead
- **GitHub CLI (`gh`)**: Authenticated with admin access to this repo (for automatic token generation)
- **macOS or Linux**: The installer supports macOS (arm64/x64) and Linux (x64/arm64)

Check your Python version:
```bash
python3 --version
```

Install and authenticate the GitHub CLI if you haven't already:
```bash
brew install gh
gh auth login
```

## Installation

From the repository root:

```bash
bash scripts/install-runner.sh
```

The installer will:
1. Check prerequisites (Python, disk space, gh authentication)
2. Download the latest GitHub Actions runner for your OS and architecture
3. Generate a registration token (or prompt you for one if `gh` is unavailable)
4. Register the runner with the label `insider-signals` (plus the default `self-hosted`)
5. Install it as a service (launchd on macOS, systemd on Linux)
6. Start the service and verify it is running

**Installation log**: `logs/install-runner.log`

## Verification

1. **Check the runner appears in GitHub**:
   Go to https://github.com/orioldc/stock-valuation-insider-signals/settings/actions/runners
   
   You should see a runner named `<hostname>-insider-signals` with status "Idle" and labels `self-hosted, insider-signals, <OS>, <arch>`.

2. **Check the service status**:
   ```bash
   cd .github-runner
   ./svc.sh status
   ```

3. **View runner logs** (if needed):
   ```bash
   tail -f .github-runner/_diag/Runner_*.log
   ```

## Scheduled Run Behavior

The workflow is scheduled to run at 06:00 UTC on the 1st of every month.

**What happens if the machine is asleep or offline?**

GitHub queues the workflow run for self-hosted runners. When the machine wakes or comes online and the runner service starts, the queued run executes. This is different from GitHub-hosted runners, which fail immediately if unavailable.

In practice: if your machine is asleep at the scheduled time, the workflow will run when you wake it. The database will be slightly delayed but not skipped.

## Uninstallation

To stop and remove the runner:

```bash
bash scripts/install-runner.sh --uninstall
```

This will:
1. Stop the service
2. Remove the service from launchd/systemd
3. Deregister the runner from GitHub
4. Delete the `.github-runner` directory

## Reinstallation / Reconfiguration

The installer is idempotent and safe to re-run. If the runner is already configured:
- By default, it will **reconfigure** (stop the service, deregister, re-register, restart)
- With `--skip-config`, it will skip reconfiguration (useful after runner software upgrades)

## Security Constraints

### No `pull_request` Trigger

**CRITICAL**: The monthly-snapshot workflow must never have a `pull_request` trigger. The workflow file includes a prominent warning comment to prevent accidental addition.

**Why?**

- This repository is public.
- The workflow runs on a self-hosted runner (the owner's machine).
- A `pull_request` trigger would allow **any fork** to propose a change that, when a PR is opened, executes arbitrary code on the owner's machine.
- `workflow_dispatch` and `schedule` triggers are safe because they only run code from the main branch, which is protected.

If you add a `pull_request` trigger, you are giving strangers code execution on your machine. Do not do this.

### Runner Isolation

The self-hosted runner:
- Uses the repository's existing Python environment and dependencies
- Writes to the repository's `data/` directory
- Has the same filesystem access as your user account

This is acceptable for a single-owner private workflow but would be unacceptable for a multi-tenant or public-contribution scenario. The runner is configured with the custom label `insider-signals` so it cannot be used by other workflows or repositories.

## Troubleshooting

### Runner Not Appearing in GitHub

- Check that `gh auth status` shows you are authenticated
- Verify you have admin access to the repository
- Check the log at `logs/install-runner.log` for errors

### Service Not Starting

On macOS:
```bash
launchctl list | grep actions.runner
```

On Linux:
```bash
systemctl --user status actions.runner.*
```

If the service is not running, check the diagnostics logs:
```bash
tail -f .github-runner/_diag/Runner_*.log
```

### Workflow Still Failing with Rate Limits

If the workflow is still hitting Yahoo Finance rate limits even on the self-hosted runner:
1. Verify the workflow is actually using the self-hosted runner (check the run logs for the runner name)
2. Check that your IP is not being blocked for other reasons (VPN, corporate network, etc.)
3. The workflow steps are marked `continue-on-error: true` for non-critical data; failures in prices/splits/market-caps are logged but do not block publication

### Disk Space

The runner's work directory is `.github-runner/_work`. Each workflow run downloads the repository and builds the database in this workspace. Old run artifacts are cleaned up automatically, but if you run low on disk space, you can manually clean the work directory:

```bash
rm -rf .github-runner/_work/*
```

The database itself lives in `data/insider_signals.db` (not in the runner's work directory) and is reused across runs.

## Maintenance

### Updating the Runner Software

GitHub periodically releases new runner versions. The installer always fetches the latest version, so to upgrade:

```bash
bash scripts/install-runner.sh
```

This will reconfigure the runner with the latest software. Alternatively, the runner auto-updates itself when it detects a new version is available (you will see update messages in the runner logs).

### Changing the Runner Name or Labels

Re-run the installer. It will detect the existing configuration and offer to reconfigure, which includes deregistering the old runner and registering a new one.

### Moving to a Different Machine

1. Uninstall the runner on the old machine:
   ```bash
   bash scripts/install-runner.sh --uninstall
   ```

2. Install the runner on the new machine:
   ```bash
   bash scripts/install-runner.sh
   ```

Only one runner with the `insider-signals` label should be active at a time. The workflow's concurrency group ensures that if two runners somehow both pick up the job, they will not run concurrently.

## Manual Workflow Dispatch

You can manually trigger the workflow from GitHub:

1. Go to https://github.com/orioldc/stock-valuation-insider-signals/actions/workflows/monthly-snapshot.yml
2. Click "Run workflow"
3. Optionally set:
   - `tag_suffix`: Append a suffix to the release tag (e.g., `-hotfix`)
   - `force_full_rebuild`: Rebuild from scratch instead of incremental refresh (slow, ~60 min)
   - `backfill_current_quarter`: Force backfill of current quarter Form 4s (slow, ~90 min)

The workflow will queue on the self-hosted runner and execute when available.

## When to Use GitHub-Hosted Runners Instead

If Yahoo Finance lifts its IP-based block or if you migrate to a different data source that works from GitHub's IPs, you can switch back to GitHub-hosted runners by changing:

```yaml
runs-on: [self-hosted, insider-signals]
```

to:

```yaml
runs-on: ubuntu-latest
```

And removing the concurrency block (GitHub-hosted runners are ephemeral and do not need concurrency control).

The self-hosted runner setup is a workaround for a specific network-level block, not a permanent architecture decision.
